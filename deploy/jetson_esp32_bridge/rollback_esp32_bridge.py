#!/usr/bin/env python3
"""Undo the last ESP32-S3 bridge install on the Jetson (Python 3 standard library only).

Default: check only. With --apply: the new bridge container is stopped (SIGTERM: it sends STOP to the ESP32 before the
port closes), set to restart 'no' and renamed '<new name>-rolledback-<stamp>' (kept); the old bridge container gets
its name and restart policy back. The old container is started only with --start-old (the old ESP8266 is no longer on
the vehicle, so it would only fail to find its device). Nothing is removed. Receipt: ROLLBACK_<stamp>.json next to the
install receipt. Exit codes: 0 done / check passed, 1 partly failed (see the receipt), 2 refused.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from install_esp32_bridge import (STOP_GRACE_S, DockerAPI, KitError, default_state_dir, utc_stamp,  # noqa: E402
                                  write_json)


def find_receipt(options):
    if options.receipt:
        path = Path(options.receipt)
        return path / 'RECEIPT.json' if path.is_dir() else path
    latest = Path(options.state_dir or default_state_dir()) / 'LATEST_RECEIPT'
    if not latest.is_file():
        raise KitError('no_receipt', str(latest))
    return Path(latest.read_text(encoding='utf-8').strip())


def plan(docker, receipt):
    target = receipt.get('target') or {}
    new_id, old_id = target.get('new_container_id'), target.get('old_container_id')
    if not new_id or not old_id:
        raise KitError('receipt_without_containers', receipt.get('result', ''))
    new, old = docker.inspect(new_id), docker.inspect(old_id)
    if old is None:
        raise KitError('old_container_missing', old_id[:12])
    holder = docker.inspect(target['old_name'])
    if holder is not None and holder['Id'] != old_id:
        raise KitError('old_name_taken', '%s is used by %s' % (target['old_name'], holder['Id'][:12]))
    return {'new': None if new is None else {'id': new['Id'], 'name': new['Name'].lstrip('/'),
                                             'running': bool((new.get('State') or {}).get('Running'))},
            'old': {'id': old['Id'], 'name': old['Name'].lstrip('/'), 'restore_name': target['old_name'],
                    'restore_restart_policy': target.get('old_restart_policy') or {'Name': 'no'}}}


def main(argv=None, docker=None, out=None):
    p = argparse.ArgumentParser(description='Undo the last ESP32-S3 bridge install (check by default)')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--receipt', default=None)
    p.add_argument('--state-dir', default=None)
    p.add_argument('--start-old', action='store_true', help='start the old bridge container again')
    p.add_argument('--docker-socket', default='/var/run/docker.sock')
    options = p.parse_args(argv)
    out = out or sys.stdout
    docker = docker or DockerAPI(options.docker_socket)
    try:
        receipt_path = find_receipt(options)
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        steps = plan(docker, receipt)
    except (KitError, OSError, ValueError) as error:
        print('되돌리기 불가: %s' % error, file=out)
        return 2
    print(json.dumps(steps, ensure_ascii=False, indent=1), file=out)
    if not options.apply:
        print('점검 통과 (바꾼 것 없음. 되돌리기는 --apply)', file=out)
        return 0
    stamp, problems, done = utc_stamp(), [], []
    target = receipt['target']
    new = steps['new']
    if new is not None:
        for name, action in (
                ('stop_new', lambda: docker.stop(new['id'], STOP_GRACE_S) if new['running'] else None),
                ('new_restart_no', lambda: docker.set_restart(new['id'], {'Name': 'no', 'MaximumRetryCount': 0})),
                ('rename_new', lambda: docker.rename(new['id'], '%s-rolledback-%s' % (target['new_name'], stamp)))):
            try:
                action()
                done.append(name)
            except KitError as error:
                problems.append('%s: %s' % (name, error))
    old = steps['old']
    for name, action in (
            ('rename_old', lambda: docker.rename(old['id'], old['restore_name']) if old['name'] != old['restore_name']
             else None),
            ('old_restart_policy', lambda: docker.set_restart(old['id'], old['restore_restart_policy'])),
            ('start_old', lambda: docker.start(old['id']) if options.start_old else None)):
        try:
            action()
            done.append(name)
        except KitError as error:
            problems.append('%s: %s' % (name, error))
    result = {'stamp': stamp, 'receipt': str(receipt_path), 'done': done, 'problems': problems,
              'result': 'rolled_back' if not problems else 'partly_failed'}
    try:
        write_json(receipt_path.parent / ('ROLLBACK_%s.json' % stamp), result)
    except OSError as error:
        print('기록을 쓰지 못함: %s' % error, file=out)
    print('결과: %s' % result['result'], file=out)
    return 0 if not problems else 1


if __name__ == '__main__':
    sys.exit(main())
