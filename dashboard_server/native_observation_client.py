"""Bounded local client for a sensor-only native observation owner.

No constructor/GET starts work. The endpoint has no motor/device commands.
Only explicit start/stop mutate this separate source owner's lifecycle.
"""
import json
import socket
import time


class NativeObservationClient:
    def __init__(self, socket_path):
        if type(socket_path) is not str or not socket_path.startswith('/') or len(socket_path.encode())>100:
            raise ValueError('bounded_absolute_native_observation_socket_required')
        self.socket_path=socket_path

    def _request(self, operation):
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                deadline=time.monotonic()+(3.5 if operation=='stop' else .6)
                client.settimeout(max(.001,deadline-time.monotonic()))
                client.connect(self.socket_path)
                client.sendall((json.dumps({'op':operation})+'\n').encode())
                parts=[];size=0
                while True:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError('native_observation_reply_deadline')
                    client.settimeout(remaining)
                    raw=client.recv(min(65536,262145-size))
                    if not raw:raise ValueError('native_observation_reply_incomplete')
                    parts.append(raw);size+=len(raw)
                    if size>262144:raise ValueError('native_observation_reply_capacity')
                    if b'\n' in raw:break
                data=b''.join(parts)
                if data.count(b'\n')!=1 or not data.endswith(b'\n'):
                    raise ValueError('native_observation_reply_shape')
                result=json.loads(data,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite_native_reply')))
                if type(result) is not dict or result.get('runtime_authority') is not False or result.get('motor_command_sent') is not False:
                    raise ValueError('native_observation_reply_contract')
                return result
        except (OSError,ValueError,OverflowError):
            return dict(ok=False,state='unavailable',error='native_observation_source_unavailable',
                input=None,runtime_authority=False,motor_command_sent=False,autonomy_output_enabled=False)

    def start(self):return self._request('start')
    def stop(self):return self._request('stop')
    def status(self):return self._request('status')
    def input(self):
        result=self._request('input')
        return result.get('input') if result.get('ok') is True else None
