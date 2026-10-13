"""Execute the actual helper with fake Docker CLI/API/UDS. No Docker or SSH."""
from copy import deepcopy
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
NAME='ubts-native-sensor-observation'
PARENT='sha256:'+'1'*64
NEW_IMAGE='sha256:'+'2'*64
OLD_ID='a'*64
NEW_ID='b'*64
LABEL='fake-reviewed-v2'


class Runtime:
    def __init__(self,folder,mode):
        self.folder=folder;self.mode=mode;self.fired=False;self.commands=[];self.api_calls=[];self.sockets=[]
        self.original=dict(Id=OLD_ID,Name='/'+NAME,Image=PARENT,RestartCount=0,
            Config=dict(Image='original-tag',Cmd=['python3','observe.py','--serve'],Env=['UNCHANGED=value'],
                Labels={'existing':'preserve'},Hostname='original-hostname',Domainname='',User='1000:1000'),
            HostConfig=dict(Binds=['original:/data:rw'],Mounts=None,NetworkMode='host',IpcMode='host',
                RestartPolicy={'Name':'on-failure','MaximumRetryCount':7},Memory=123456,NanoCpus=2000000000,
                ReadonlyRootfs=True,SecurityOpt=['no-new-privileges'],CapAdd=None,CapDrop=['ALL'],
                Privileged=False,CpuShares=128,PidsLimit=64),State=dict(Running=True,Paused=False))
        self.before=deepcopy(self.original);self.containers={OLD_ID:self.original}
        self.copied_bytes=b'old runtime file\n'

    def find(self,identifier):
        if identifier in self.containers:return self.containers[identifier]
        for value in self.containers.values():
            if value['Name'].lstrip('/')==identifier:return value
        raise KeyError(identifier)

    def fail(self,mode):
        if self.mode==mode and not self.fired:
            self.fired=True;return True
        return False

    def subprocess(self,args,**kwargs):
        self.commands.append(list(args));assert args[0]=='docker',args
        code=0;output=''
        try:
            if args[1]=='inspect':output=json.dumps([deepcopy(self.find(args[2]))])
            elif args[1]=='cp':Path(args[3]).write_bytes(self.copied_bytes)
            elif args[1]=='build':
                if self.fail('build_failure'):code=1;output='fake build failure'
            elif args[1:3]==['image','inspect']:output=json.dumps([{'Id':NEW_IMAGE}])
            elif args[1]=='run':
                if 'sha256sum' in args:
                    targets=args[args.index(NEW_IMAGE)+1:]
                    raw=(self.folder/'files/example.py').read_bytes()
                    output='\n'.join(hashlib.sha256(raw).hexdigest()+'  '+p for p in targets)+'\n'
                else:output='map_sources_admitted True fake-digest\n'
            elif args[1]=='update':
                value=self.find(args[-1]);assert args[2].startswith('--restart=')
                policy=args[2].split('=',1)[1].split(':')
                value['HostConfig']['RestartPolicy']={'Name':policy[0],'MaximumRetryCount':int(policy[1])if len(policy)>1 else 0}
            elif args[1]=='stop':
                value=self.find(args[-1])
                if value['Id']==OLD_ID and self.fail('old_stop_failure'):code=1;output='fake original stop failure'
                else:value['State']['Running']=False
            elif args[1]=='rename':
                value=self.find(args[2]);wanted=args[3]
                if value['Id']==OLD_ID and self.fail('old_rename_failure'):code=1;output='fake original rename failure'
                else:
                    assert not any(v['Name'].lstrip('/')==wanted for v in self.containers.values() if v is not value)
                    value['Name']='/'+wanted
            elif args[1]=='start':self.find(args[2])['State']['Running']=True
            elif args[1]=='restart':self.find(args[-1])['State']['Running']=True
            elif args[1]=='logs':output='clean normal restart\n'
            else:raise AssertionError(args)
        except KeyError:
            code=1;output='fake container not found'
        return subprocess.CompletedProcess(args,code,stdout=output.encode())

    def api(self,method,path,body):
        self.api_calls.append((method,path,deepcopy(body)))
        if method=='GET' and path=='/containers/json?all=1':
            return 200,[{'Id':v['Id'],'Names':[v['Name']]} for v in self.containers.values()]
        if path.startswith('/containers/create?'):
            if self.fail('create_failure'):return 500,{'message':'fake create failure'}
            self.containers[NEW_ID]=dict(Id=NEW_ID,Name='/'+NAME,Image=body['Image'],RestartCount=0,
                Config=deepcopy({k:v for k,v in body.items() if k!='HostConfig'}),
                HostConfig=deepcopy(body['HostConfig']),State=dict(Running=False,Paused=False))
            if self.fail('create_reply_lost'):return 500,{'message':'create completed but reply lost'}
            return 201,{'Id':NEW_ID}
        parts=path.split('/');value=self.find(parts[2]);operation=parts[3]
        if operation=='start':
            value['State']['Running']=True
            if self.fail('new_start_failure'):return 500,{'message':'start completed but reply lost'}
            return 204,None
        if operation=='update':
            assert set(body)=={'RestartPolicy'},body
            value['HostConfig']['RestartPolicy']=deepcopy(body['RestartPolicy']);return 200,{}
        raise AssertionError((method,path,body))

    def socket(self,*args,**kwargs):
        runtime=self
        class FakeSocket:
            def __init__(self):self.timeout=None;self.path=None;self.request=None
            def settimeout(self,value):self.timeout=value
            def connect(self,path):
                self.path=path
                if path=='/var/run/docker.sock':
                    # A real blocking UDS would otherwise make HTTP timeout ineffective.
                    if self.timeout!=60:raise AssertionError('Docker API UDS timeout not set before connect')
                else:assert self.timeout==3
                runtime.sockets.append(self)
            def sendall(self,body):self.request=json.loads(body)
            def recv(self,n):
                value=runtime.find(NAME)
                assert value['State']['Running']
                if self.request['op']=='status':
                    return json.dumps({'ok':True,'state':'idle','status':{'owner':{'owned':False}},
                        'runtime_authority':False,'motor_command_sent':False}).encode()+b'\n'
                return json.dumps({'ok':True,'available':False,'runtime_authority':False}).encode()+b'\n'
            def close(self):pass
        return FakeSocket()

    def http_class(self):
        runtime=self
        class FakeHTTP:
            def __init__(self,host,timeout=60):self.timeout=timeout;self.sock=None
            def request(self,method,path,body=None,headers=None):
                self.connect();self.call=(method,path,None if body is None else json.loads(body))
            def getresponse(self):
                code,data=runtime.api(*self.call)
                return SimpleNamespace(status=code,read=lambda:b'' if data is None else json.dumps(data).encode())
            def close(self):self.sock.close()
        return FakeHTTP


class InstallerRollbackTests(unittest.TestCase):
    def execute(self,mode,*,dry=False):
        runs=ROOT/'fake-runs';runs.mkdir(exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix=mode+'-',dir=runs)).resolve()
        assert folder.is_relative_to(ROOT.resolve())
        (folder/'files').mkdir();(folder/'files/example.py').write_bytes(b'new runtime file\n')
        spec={'parent':PARENT,'tag':'fake-tag','label':LABEL,
              'files':{'files/example.py':'/opt/example.py'},'tests':[]}
        (folder/'OVERLAY.json').write_text(json.dumps(spec))
        runtime=Runtime(folder,mode);namespace={'__file__':str(folder/'install_native_overlay.py'),'__name__':'__main__'}
        fcntl=ModuleType('fcntl');fcntl.LOCK_EX=2;fcntl.LOCK_NB=4;fcntl.flock=lambda *a:None
        exit_code=None
        with patch.dict(sys.modules,{'fcntl':fcntl}),patch.object(subprocess,'run',runtime.subprocess), \
             patch.object(socket,'socket',runtime.socket),patch.object(socket,'AF_UNIX',1,create=True), \
             patch.object(http.client,'HTTPConnection',runtime.http_class()), \
             patch.object(sys,'argv',[str(folder/'install_native_overlay.py')]+(['--dry-run'] if dry else [])):
            try:exec(compile((ROOT/'install_native_overlay.py').read_bytes(),str(folder/'install_native_overlay.py'),'exec'),namespace)
            except SystemExit as error:exit_code=error.code
            finally:
                if 'LOCK_FD' in namespace:os.close(namespace['LOCK_FD'])
        result=json.loads((folder/('DRY_RUN_RESULT.json'if dry else'INSTALL_RESULT.json')).read_text())
        return runtime,result,namespace,exit_code,folder

    def assert_restored(self,runtime,result):
        value=runtime.containers[OLD_ID]
        self.assertEqual(value['Name'],'/'+NAME)
        self.assertEqual(value['Image'],runtime.before['Image'])
        self.assertEqual(value['Config'],runtime.before['Config'])
        self.assertEqual(value['HostConfig'],runtime.before['HostConfig'])
        self.assertEqual(value['State']['Running'],runtime.before['State']['Running'])
        self.assertTrue(next(x for x in result['steps']if x['step']=='rollback')['ok'])
        self.assertFalse(result['ok'])

    def test_restart_update_then_stop_failure_restores_exact_policy_without_name_change(self):
        r,result,_,code,_=self.execute('old_stop_failure')
        self.assertEqual(code,1);self.assert_restored(r,result)
        self.assertFalse(any(a[1]=='rename'for a in r.commands))
        self.assertEqual(r.original['HostConfig']['RestartPolicy'],{'Name':'on-failure','MaximumRetryCount':7})

    def test_stop_success_then_rename_failure_restores_running_original_ID(self):
        r,result,_,code,_=self.execute('old_rename_failure')
        self.assertEqual(code,1);self.assert_restored(r,result)
        self.assertIn(['docker','start',OLD_ID],r.commands)

    def test_rename_success_then_create_failure_restores_name_policy_and_running_state(self):
        r,result,_,code,_=self.execute('create_failure')
        self.assertEqual(code,1);self.assert_restored(r,result)
        self.assertIn(['docker','rename',OLD_ID,NAME],r.commands)
        self.assertNotIn(NEW_ID,r.containers)

    def test_Docker_API_unix_socket_has_timeout_before_connect(self):
        r,result,_,code,_=self.execute('success')
        self.assertIsNone(code);self.assertTrue(result['ok'])
        api_sockets=[s for s in r.sockets if s.path=='/var/run/docker.sock']
        self.assertGreaterEqual(len(api_sockets),2)
        self.assertTrue(all(s.timeout==60 for s in api_sockets))
        self.assertEqual(result['rollback']['original_restart_policy'],r.before['HostConfig']['RestartPolicy'])
        self.assertIn('docker update --restart=on-failure:7 '+NAME,result['rollback']['steps'])

    def test_failed_owned_new_container_is_retained_with_inspect_evidence(self):
        r,result,_,code,folder=self.execute('new_start_failure')
        self.assertEqual(code,1);self.assert_restored(r,result)
        self.assertIn(NEW_ID,r.containers)
        self.assertFalse(r.containers[NEW_ID]['State']['Running'])
        self.assertTrue(r.containers[NEW_ID]['Name'].startswith('/'+NAME+'-failed-'+LABEL+'-'))
        proofs=list(folder.glob('rollback-replacement-*.inspect.private.json'))
        self.assertEqual(len(proofs),1);self.assertEqual(json.loads(proofs[0].read_text())['Id'],NEW_ID)
        self.assertFalse(any(a[1]=='rm'for a in r.commands))

    def test_create_response_lost_finds_only_proven_own_replacement_and_retains_it(self):
        r,result,_,code,_=self.execute('create_reply_lost')
        self.assertEqual(code,1);self.assert_restored(r,result)
        self.assertIn(NEW_ID,r.containers);self.assertIn('-failed-',r.containers[NEW_ID]['Name'])

    def test_failure_before_first_mutation_does_not_run_rollback_or_touch_original(self):
        r,result,_,code,_=self.execute('build_failure')
        self.assertEqual(code,1);self.assertEqual(r.original,r.before)
        self.assertFalse(any(x['step']=='rollback'for x in result['steps']))
        self.assertFalse(any(a[1]in('update','stop','rename','start')for a in r.commands))

    def test_restore_helper_preserves_original_stopped_state_instead_of_starting(self):
        r,_,ns,_,_=self.execute('success',dry=True)
        original=deepcopy(r.before);original['State']['Running']=False
        r.original['State']['Running']=False
        r.original['HostConfig']['RestartPolicy']={'Name':'no','MaximumRetryCount':0}
        with patch.object(subprocess,'run',r.subprocess),patch.object(socket,'socket',r.socket),patch.object(socket,'AF_UNIX',1,create=True):
            restored=ns['restore_original'](original)
        self.assertFalse(restored['running']);self.assertEqual(r.original['HostConfig'],original['HostConfig'])
        self.assertFalse(any(a[1]=='start'for a in r.commands))


if __name__=='__main__':unittest.main()
