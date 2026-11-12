#!/usr/bin/env python3
"""Put back the dashboard container that install_dashboard.py kept (Python 3 standard library only).

Default: check only. With --apply:
  1. idle check (through the running dashboard; if it does not answer, through the recorder and the
     rover bridge status file directly)
  2. the installed container is stopped, gets restart policy 'no' and is renamed
     '<name>-rolledback-<stamp>' (kept, not removed)
  3. the kept old container gets its name and original restart policy back and starts
  4. /api/health and an idle check on the restored dashboard
The result is written as ROLLBACK_<stamp>.json next to the install receipt.
No compose, prune, pull, removal, motor or arm command is used.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_dashboard as kit  # noqa: E402

ROLLBACK_SCHEMA = 'ubts.dashboard_kit.rollback.v1'
ROLLBACK_DEADLINE_S = 240.0


def find_receipt(state_dir, explicit=None):
    """The newest receipt whose install finished and that has not been rolled back yet."""
    if explicit:
        path = Path(explicit)
        if path.is_dir():
            path = path / 'RECEIPT.json'
        return path, json.loads(path.read_bytes())
    state_dir = Path(state_dir)
    kit.need(state_dir.is_dir(), 'state_dir_missing', str(state_dir))
    for run in sorted((p for p in state_dir.iterdir() if p.is_dir()), reverse=True):
        path = run / 'RECEIPT.json'
        if not path.is_file():
            continue
        try:
            receipt = json.loads(path.read_bytes())
        except ValueError:
            continue
        if receipt.get('result') != 'installed':
            continue
        done = False
        for item in run.glob('ROLLBACK_*.json'):
            try:
                done = done or json.loads(item.read_bytes()).get('ok') is True
            except ValueError:
                pass
        if not done:
            return path, receipt
    raise kit.KitError('no_installed_receipt', str(state_dir))


def plan_rollback(docker, receipt):
    kit.need(receipt.get('schema') == kit.RECEIPT_SCHEMA, 'receipt_schema')
    kit.need(receipt.get('result') == 'installed', 'receipt_not_installed', receipt.get('result'))
    name = receipt['target']['name']
    old_id = receipt['target']['old_container_id']
    old_image = receipt['target']['old_image_id']
    new_id = receipt['containers']['new']['id']
    old = docker.inspect(old_id)
    kit.need(old is not None, 'kept_container_missing', old_id[:12])
    kit.need(old.get('Image') == old_image, 'kept_container_image_changed')
    current = docker.inspect(name)
    kit.need(current is not None, 'current_container_missing', name)
    kit.need(current['Id'] == new_id, 'current_is_not_this_install',
             '%s 이름의 컨테이너가 이 영수증의 새 컨테이너가 아님(나중에 다른 설치가 있었나?)' % name)
    kit.need(current.get('Image') == receipt['images']['new'], 'current_image_changed')
    return dict(name=name, old_id=old_id, old_image=old_image, new_id=new_id,
                old_name_now=old.get('Name', '').lstrip('/'), old_running=kit.running_state(old),
                old_policy=receipt['target']['old_restart_policy'], current=current, old=old)


def fallback_idle(docker, http, plan):
    """When the installed dashboard does not answer: recorder /status and the rover bridge file."""
    out = {}
    recorder = kit.env_value(plan['current'], 'RECORDER_API_URL', 'http://127.0.0.1:8790').rstrip('/')
    rec = kit.get_json(http, recorder + '/status')
    kit.need(rec.get('state') == 'idle' and rec.get('active') is False and rec.get('finalizing') in (False, None),
             'not_idle', 'recorder state=%s active=%s' % (rec.get('state'), rec.get('active')))
    out['recorder'] = 'idle(direct)'
    raw = kit.bridge_status_bytes(docker, plan['current'])
    kit.need(raw is not None, 'not_idle', 'rover bridge status file not readable')
    value = json.loads(raw)
    kit.need(value.get('armed') is False and value.get('moving') is False, 'not_idle', 'rover bridge armed/moving')
    out['rover_bridge_file'] = 'disarmed_stopped'
    return out


def reinstate_installed(docker, plan, log):
    """If the old container could not be brought back, start the installed one again (best effort)."""
    try:
        old = docker.inspect(plan['old_id'])
        if old is not None and kit.running_state(old):
            return 'not_needed'
        new = docker.inspect(plan['new_id'])
        if new is None:
            return 'installed_container_missing'
        if new.get('Name') != '/' + plan['name']:
            if docker.inspect(plan['name']) is not None:
                return 'name_taken'
            docker.rename(plan['new_id'], plan['name'])
        docker.set_restart(plan['new_id'], plan['current']['HostConfig']['RestartPolicy'])
        if not kit.running_state(docker.inspect(plan['new_id'])):
            docker.start(plan['new_id'])
        log('설치했던 컨테이너를 다시 시작함(대시보드가 꺼진 채로 두지 않기 위해)')
        return 'restarted_installed'
    except BaseException as error:  # noqa: BLE001
        return 'failed:' + str(getattr(error, 'code', type(error).__name__))


def main(argv=None, *, docker=None, http=None, clock=time.monotonic, sleep=time.sleep, out=None):
    p = argparse.ArgumentParser(description='UBTS 대시보드 되돌리기. 기본은 점검만 한다.')
    p.add_argument('--apply', action='store_true', help='실제로 되돌린다(없으면 점검만)')
    p.add_argument('--receipt', default=None, help='영수증 파일 또는 폴더(기본: 가장 최근 설치)')
    p.add_argument('--state-dir', default=None, help='영수증 폴더 (기본: ~/ubts-releases/dashboard-kit)')
    p.add_argument('--docker-socket', default=kit.DEFAULT_SOCKET)
    p.add_argument('--dashboard-url', default=None)
    p.add_argument('--skip-idle-check', action='store_true',
                   help='쉬는 상태 확인을 건너뜀. 차가 멈춰 있고 조종하는 사람이 없을 때만')
    p.add_argument('--deadline', type=float, default=ROLLBACK_DEADLINE_S)
    p.add_argument('--json', action='store_true')
    opt = p.parse_args(argv)
    out = out or sys.stdout
    docker = docker or kit.DockerAPI(opt.docker_socket)
    http = http or kit.http_get
    state_dir = Path(opt.state_dir) if opt.state_dir else kit.default_state_dir()
    try:
        kit.need(30.0 <= opt.deadline <= 900.0, 'option_deadline_range')
        receipt_path, receipt = find_receipt(state_dir, opt.receipt)
        plan = plan_rollback(docker, receipt)
        print('영수증 %s' % receipt_path, file=out)
        print('지금 컨테이너 %s (%s, 묶음 %s) -> 보존된 %s (%s, 이미지 %s) 로 되돌림' % (
            plan['name'], plan['new_id'][:12], receipt['bundle']['bundle_id'][:12], plan['old_name_now'],
            plan['old_id'][:12], plan['old_image'][7:19]), file=out)
        base = kit.dashboard_url(plan['current'], opt.dashboard_url)
        idle = None
        if opt.skip_idle_check:
            idle = {'skipped_by_option': True}
        else:
            try:
                idle = kit.idle_check(http, base, bridge_status=kit.bridge_status_bytes(docker, plan['current']))
            except kit.KitError as error:
                if error.code == 'not_idle':
                    raise
                print('대시보드가 답하지 않아 녹화기와 브리지 상태 파일로 확인합니다 (%s)' % error.code, file=out)
                idle = fallback_idle(docker, http, plan)
        print('쉬는 상태 확인: ' + ', '.join('%s=%s' % kv for kv in sorted(idle.items())), file=out)
        if not opt.apply:
            print('\n점검 통과. 되돌리려면 같은 명령에 --apply 를 붙이세요.', file=out)
            return 0
        stamp = kit.utc_stamp()
        deadline = kit.Deadline(opt.deadline, clock)
        result = dict(schema=ROLLBACK_SCHEMA, ok=False, stamp=stamp, started_utc=kit.utc_now(),
                      receipt=str(receipt_path), name=plan['name'], from_container=plan['new_id'],
                      to_container=plan['old_id'], to_image=plan['old_image'], idle_before=idle,
                      compose_used=False, prune_used=False, removal_used=False, motor_or_arm_command_sent=False)
        result_path = receipt_path.parent / ('ROLLBACK_%s.json' % stamp)
        events = []

        def log(text):
            line = '[%s] %s' % (time.strftime('%H:%M:%S'), text)
            events.append(line)
            print(line, file=out, flush=True)

        try:
            result['restore'] = kit.restore_old(
                docker, http, name=plan['name'], old_id=plan['old_id'], old_image=plan['old_image'],
                old_policy=plan['old_policy'], new_id=plan['new_id'], new_label='rolledback', stamp=stamp,
                deadline=deadline, log=log, sleep=sleep, base_override=opt.dashboard_url)
            restored = docker.inspect(plan['old_id'])
            result['idle_after'] = kit.idle_check(http, kit.dashboard_url(restored, opt.dashboard_url),
                                                  bridge_status=kit.bridge_status_bytes(docker, restored))
            result['ok'] = True
            log('되돌리기 완료: %s 가 다시 돌고 있음. 설치했던 컨테이너는 %s-rolledback-%s 로 보존' % (
                plan['old_id'][:12], plan['name'], stamp))
        except BaseException as error:  # noqa: BLE001 - record every failure
            result['error'] = dict(code=getattr(error, 'code', type(error).__name__), detail=str(error)[:400])
            log('!! 되돌리기 실패: %s' % error)
            result['reinstate_installed'] = reinstate_installed(docker, plan, log)
        finally:
            result['finished_utc'] = kit.utc_now()
            result['events'] = events
            kit.write_json(result_path, result)
        if opt.json:
            print(json.dumps(result, ensure_ascii=False, indent=1), file=out)
        print('결과: %s (%s)' % ('rolled_back' if result['ok'] else 'rollback_failed', result_path), file=out)
        return 0 if result['ok'] else 3
    except kit.KitError as error:
        print('실패: %s' % error, file=out)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
