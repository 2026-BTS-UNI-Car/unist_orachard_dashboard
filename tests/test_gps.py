import asyncio
import gzip
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from dashboard_server.gps import GnssDecoder, GPS_SCHEMA, fresh_snapshot, ubx_packet
from dashboard_server.app import create_server
from dashboard_server.offline_maps import asset_path, map_status
from scripts.gps_ws_bridge import GpsBridgeState, stream


def nmea(body):
    checksum = 0
    for value in body.encode(): checksum ^= value
    return f'${body}*{checksum:02X}\r\n'.encode()


def pvt(quality=4, tow=123456):
    data = bytearray(92)
    struct.pack_into('<IHBBBBBB', data, 0, tow, 2026, 9, 26, 1, 2, 3, 3)
    data[20] = 3
    data[21] = {0:0, 1:1, 2:3, 4:131, 5:67}[quality]
    data[23] = 23
    struct.pack_into('<iiiiII', data, 24, 1291900000, 355700000, 55000, 29000, 14, 25)
    struct.pack_into('<ii', data, 60, 1250, 4500000)
    return ubx_packet(1, 7, data)


def sample(**kwargs):
    return dict(schema=GPS_SCHEMA, valid=True, latitude=35.57, longitude=129.19,
                quality=4, received_at=time.time(), **kwargs)


class DecoderTests(unittest.TestCase):
    def test_fragmented_mixed_stream_checksum_and_rtk(self):
        decoder = GnssDecoder()
        good = nmea('GNGGA,010203.00,3534.2000,N,12911.4000,E,4,23,0.6,29.0,M,26.0,M,,')
        stream_bytes = b'garbage'+good[:-4]+b'00\r\n'+good+pvt(5)
        frames = []
        for i in range(0, len(stream_bytes), 3): frames.extend(decoder.feed(stream_bytes[i:i+3]))
        self.assertEqual(len(frames), 2)
        self.assertEqual(decoder.rejected, 1)
        self.assertEqual(frames[0]['fix_type'], 'rtk_fixed')
        self.assertAlmostEqual(frames[0]['latitude'], 35.57)
        self.assertEqual(frames[0]['altitude_ellipsoid_m'], 55)
        self.assertIsNone(frames[0]['h_acc_m'])  # HDOP is not meters.
        self.assertEqual(frames[1]['fix_type'], 'rtk_float')
        self.assertAlmostEqual(frames[1]['h_acc_m'], .014)
        self.assertEqual(frames[1]['gps_time'], '2026-09-26T01:02:03+00:00')

    def test_no_fix_and_invalid_coordinates_never_become_origin(self):
        decoder = GnssDecoder()
        for body in ['GNGGA,010203,,,,,0,00,,,,,,,',
                     'GNGGA,010203,9160,N,18100,E,1,10,1,0,M,0,M,,']:
            frame = decoder.feed(nmea(body))[0]
            self.assertFalse(frame['valid']); self.assertIsNone(frame['latitude'])
        self.assertEqual(decoder.feed(nmea('GNGGA,010203,3534,E,12911,N,1,10,1,0,M,0,M,,')), [])
        frame = decoder.feed(nmea('GNGGA,010203,0000,N,00000,E,1,10,1,0,M,0,M,,'))[0]
        self.assertTrue(frame['valid']); self.assertEqual(frame['latitude'], 0)

    def test_gst_refines_only_same_epoch(self):
        decoder = GnssDecoder()
        decoder.feed(nmea('GNGGA,010203,3534,N,12911,E,1,10,1,0,M,0,M,,'))
        self.assertEqual(decoder.feed(nmea('GNGST,010202,1,1,1,0,3,4,6')), [])
        frame = decoder.feed(nmea('GNGST,010203,1,1,1,0,3,4,6'))[0]
        self.assertEqual(frame['h_acc_m'], 5)
        self.assertEqual(frame['v_acc_m'], 6)

    def test_hppos_requires_recent_matching_pvt_and_preserves_fix(self):
        now = [100.0]; decoder = GnssDecoder(clock=lambda: now[0])
        hp = bytearray(36)
        struct.pack_into('<Iiiii', hp, 4, 123456, 1291900000, 355700000, 55000, 29000)
        struct.pack_into('<bbbbII', hp, 24, 9, -3, 1, -1, 120, 240)
        packet = ubx_packet(1, 20, hp)
        self.assertEqual(decoder.feed(packet), [])
        decoder.feed(pvt())
        frame = decoder.feed(packet)[0]
        self.assertAlmostEqual(frame['latitude'], 35.569999997)
        self.assertAlmostEqual(frame['h_acc_m'], .012)
        self.assertEqual(frame['quality'], 4)
        now[0] += 3
        self.assertEqual(decoder.feed(packet), [])
        decoder.feed(pvt(0))
        self.assertFalse(decoder.feed(packet)[0]['valid'])

    def test_invalid_llh_checksum_and_oversize(self):
        decoder = GnssDecoder()
        data = bytearray(pvt()[6:-2]); data[78] = 1
        self.assertFalse(decoder.feed(ubx_packet(1, 7, data))[0]['valid'])
        self.assertEqual(decoder.feed(pvt()[:-1]+b'\0'), [])
        decoder.feed(b'\xb5\x62\x01\x07\xff\xff'+b'x'*10000)
        self.assertLess(len(decoder.buffer), 1024)
        self.assertTrue(decoder.feed(pvt())[0]['valid'])

    def test_two_dimensional_fix_does_not_claim_altitude(self):
        decoder = GnssDecoder()
        data = bytearray(pvt()[6:-2]); data[20] = 2
        frame = decoder.feed(ubx_packet(1,7,data))[0]
        self.assertTrue(frame['valid'])
        self.assertIsNone(frame['altitude_ellipsoid_m'])
        self.assertIsNone(frame['v_acc_m'])

    def test_stale_invalid_times_and_delayed_bridge_messages(self):
        data = sample()
        empty_snapshot = fresh_snapshot({}, None, now=100)
        self.assertEqual(fresh_snapshot(empty_snapshot, None, now=101)['state'], 'no_data')
        self.assertFalse(fresh_snapshot(data, 90, now=100)['valid'])
        for stamp in (None, float('nan'), True, 200, 'garbage'):
            self.assertFalse(fresh_snapshot(data, stamp, now=100)['valid'])
        bridge = GpsBridgeState()
        data['received_at'] = time.time()-10
        bridge.update(SimpleNamespace(data=json.dumps(data)))
        self.assertEqual(bridge.snapshot()['state'], 'stale')
        bridge.update(SimpleNamespace(data=json.dumps(dict(data, received_at='bad'))))
        self.assertEqual(bridge.snapshot()['received_at'], data['received_at'])
        self.assertFalse(bridge.snapshot()['valid'])

    @unittest.skipIf(sys.platform == 'win32', 'Linux UART implementation')
    def test_pty_single_owner_and_only_read_only_polls(self):
        import pty
        from scripts.gps_ros2_driver import GpsSerial
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        os.set_blocking(master, False)
        with tempfile.TemporaryDirectory() as temp:
            serial = GpsSerial(path, 38400, str(Path(temp)/'serial.lock'))
            other = GpsSerial(path, 38400, str(Path(temp)/'serial.lock'))
            try:
                serial.open()
                with self.assertRaises(OSError): other.open()
                serial.read()
                self.assertEqual(os.read(master, 4096), ubx_packet(10, 4)+ubx_packet(1, 7)+ubx_packet(1, 20))
                os.write(master, pvt())
                deadline = time.monotonic()+1; frames = []
                while not frames and time.monotonic() < deadline:
                    frames.extend(serial.read()); time.sleep(.01)
                self.assertTrue(frames[0]['valid'])
                self.assertFalse(serial.snapshot()['driver']['configuration_writes'])
            finally:
                serial.close(); other.close(); os.close(master); os.close(slave)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_ack_backpressure_latest_sample_and_timeout(self):
        import websockets
        state = GpsBridgeState(); state.update(SimpleNamespace(data=json.dumps(sample())))
        async with websockets.serve(lambda ws: stream(ws, state), '127.0.0.1', 0, close_timeout=.1) as server:
            port = server.sockets[0].getsockname()[1]
            async with websockets.connect(f'ws://127.0.0.1:{port}/ws/gps?profile=detail') as client:
                first = json.loads(await client.recv())
                with self.assertRaises(asyncio.TimeoutError): await asyncio.wait_for(client.recv(), .3)
                state.update(SimpleNamespace(data=json.dumps(dict(sample(), latitude=35.6))))
                await client.send(json.dumps({'ack':first['stream_seq']}))
                newest = json.loads(await client.recv())
                self.assertEqual(newest['latitude'], 35.6)
                self.assertEqual(newest['stream_seq'], 2)
                with self.assertRaises(websockets.ConnectionClosed): await asyncio.wait_for(client.recv(), 3)


class OfflineApiTests(unittest.TestCase):
    def test_range_assets_missing_bundle_and_confinement(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, OFFLINE_MAP_DIR=temp):
            root = Path(temp); (root/'korea.pmtiles').write_bytes(b'PMTiles\x03'+bytes(range(256)))
            self.assertFalse(map_status()['ok'])
            self.assertIsNone(asset_path('../not-a-map.txt'))
            server = create_server('127.0.0.1', 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            url = f'http://127.0.0.1:{server.server_port}'
            try:
                request = urllib.request.Request(url+'/maps/korea.pmtiles', headers={'Range':'bytes=0-7'})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 206); self.assertEqual(response.read(), b'PMTiles\x03')
                    self.assertEqual(response.headers['Content-Range'], 'bytes 0-7/264')
                request = urllib.request.Request(url+'/maps/korea.pmtiles', headers={'Range':'bytes=-4'})
                self.assertEqual(urllib.request.urlopen(request).read(), bytes(range(252, 256)))
                request = urllib.request.Request(url+'/maps/korea.pmtiles', method='HEAD')
                with urllib.request.urlopen(request) as response: self.assertEqual(response.headers['Content-Length'], '264')
                for route, headers, code in [('/maps/korea.pmtiles', {}, 400),
                        ('/maps/korea.pmtiles', {'Range':'bytes=999-'}, 416),
                        ('/maps/korea.pmtiles', {'Range':'bytes=0-7','If-Match':'"old"'}, 412),
                        ('/maps/%2e%2e/secret.txt', {}, 404)]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(urllib.request.Request(url+route, headers=headers))
                    self.assertEqual(error.exception.code, code)
                with urllib.request.urlopen(url+'/static/vendor/maplibre-gl/maplibre-gl.mjs') as response:
                    self.assertIn('javascript', response.headers['Content-Type'])
                    original = response.read()
                request = urllib.request.Request(url+'/static/vendor/maplibre-gl/maplibre-gl.mjs', headers={'Accept-Encoding':'gzip'})
                with urllib.request.urlopen(request) as response:
                    compressed = response.read(); etag = response.headers['ETag']
                    self.assertEqual(gzip.decompress(compressed), original)
                    self.assertLess(len(compressed), len(original)/2)
                request.add_header('If-None-Match',etag)
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
                self.assertEqual(error.exception.code,304)
            finally:
                server.shutdown(); server.server_close(); thread.join(2)


if __name__ == '__main__': unittest.main()
