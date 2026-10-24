"""Root fresh-image import-only preflight; no Start, socket, sensor or motor."""
import argparse,copy,hashlib,importlib,json,sys,threading
from pathlib import Path
from types import SimpleNamespace

def main():
    p=argparse.ArgumentParser();p.add_argument('--root-import-only',action='store_true');p.add_argument('--role',choices=('dashboard','stream'));a=p.parse_args()
    if not a.root_import_only:
        print('{"default_OFF":true,"Start_or_Arm":false}');return
    assert a.role,'explicit_role_required'
    if a.role=='dashboard':
        from dashboard_server.field10_shadow_preflight import check
        from dashboard_server.autonomy_observation import AutonomyObservation
        from dashboard_server.native_observation_client import NativeObservationClient
        result=check();assert result is not False,'field10_preflight_required'
        status=AutonomyObservation().status()
        assert status['processing_diagnostics'] is None and not status['active'] and not status['owned']
        assert callable(NativeObservationClient.control_comparison)
        assert AutonomyObservation().comparison_provider is None
    else:
        assert sys.version_info>=(3,10)
        row=dict(context=dict(source_session='image-preflight',epoch='image-preflight-epoch',generation=1,
            source_clock_domain='GENERATED',receipt_clock_domain='GENERATED_RAW'),
            scan=dict(sequence=1,frame_id='laser',stamp_ns=1000000000,receipt_ns=100000000000,ranges=None),
            imu=dict(stamp_ns=999000000,receipt_ns=99999000000),
            motion=dict(previous_scan_stamp_ns=900000000,current_scan_stamp_ns=1000000000,receipt_ns=100000000000))
        import native_observation_service as service
        for name in ('stream_rows','stream_rows_map'):
            m=importlib.import_module(name);c=m.StreamObserverRows
            assert callable(c.proposal_input) and callable(c.proposal_comparison)
            adapter=c.__new__(c);adapter._lock=threading.RLock();adapter._stopping=threading.Event()
            adapter.fault=None;adapter._proposal_input=copy.deepcopy(row);adapter._binding=(True,)*5
            for feature in ('preparation','receiver','final','nearest','registration'):
                setattr(adapter,'_factory_'+feature+'_feature',lambda:True)
            adapter.ingress=SimpleNamespace(source_now=lambda:1001000000,
                raw=SimpleNamespace(sample=lambda:SimpleNamespace(ns=100001000000)))
            owner=SimpleNamespace(session='preflight',generation=1,owned=True,cancelled=False,first_failure_reason=None)
            runner=SimpleNamespace(lock=threading.RLock(),lifecycle=SimpleNamespace(status=lambda:owner),
                resource=SimpleNamespace(context=SimpleNamespace(session='preflight',generation=1),adapter=adapter))
            result=service.read_control_comparison(runner)
            assert result['schema']=='ubts.native_observation.control_comparison.v1' and 'ranges' not in result['scan']
            owner.cancelled=True;assert service.read_control_comparison(runner) is None
    print(json.dumps(dict(ok=True,role=a.role,mode='IMPORT_AND_GENERATED_SCALAR_ONLY',Start_or_Arm=False,
        SDK_ROS_node_or_socket_started=False,live_sensor_or_actuator_used=False)))
if __name__=='__main__':main()
