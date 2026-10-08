"""Keep actual map-cell wire bytes compatible with existing dashboard clients."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import unittest

import numpy as np

from dashboard_server.tile_transport import encode_cells


def original_encoder():
    path = Path(__file__).parent/'test_fixture/native_cell_encoding_prechange.py'
    if hashlib.sha256(path.read_bytes()).hexdigest() != '272ac4c9ccbade692a39517be228e19dee37ecb488c9b6d21618e4187bce41cb':
        raise ValueError('cell_encoding_reference_changed')
    tree = ast.parse(path.read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'encode_cells')
    namespace = dict(np=np, json=json, base64=base64)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['encode_cells']


def decode(packet):
    if packet['encoding'] == 'rle2-v1':
        values = packet['cells']
        return np.repeat(values[::2], values[1::2]).astype(np.uint8)
    raw = base64.b64decode(packet['cells'], validate=True)
    if packet['encoding'] != 'packed2-v1' or len(raw) != 1024:
        raise ValueError('invalid_cell_wire_payload')
    return np.fromiter(((raw[i//4] >> (2*(i%4))) & 3 for i in range(4096)),
                       dtype=np.uint8, count=4096)


class CellEncodingTests(unittest.TestCase):
    def test_byte_compatibility_and_round_trip_across_tile_entropy(self):
        original = original_encoder()
        rng = np.random.default_rng(20261009)
        tiles = [np.full(4096, state, np.uint8) for state in range(4)]
        tiles += [np.arange(4096, dtype=np.uint16).astype(np.uint8) % 4]
        for n in range(48):
            tiles.append(rng.integers(0, 4, 4096, dtype=np.uint8))
            sparse = np.zeros(4096, dtype=np.uint8)
            sparse[rng.choice(4096, n+1, replace=False)] = 2
            tiles.append(sparse)
        for tile in tiles:
            expected, actual = original(tile), encode_cells(tile)
            self.assertEqual(json.dumps(actual, separators=(',', ':')),
                             json.dumps(expected, separators=(',', ':')))
            np.testing.assert_array_equal(decode(actual), tile)

    def test_exact_wire_length_tie_keeps_numeric_rle(self):
        for tens, length in ((45,1369), (46,1370), (47,1371)):
            remaining = 329-tens
            counts = [4096-10*tens-remaining]+[10]*tens+[1]*remaining
            states = np.repeat(np.arange(330)%4, counts).astype(np.uint8)
            numeric = np.column_stack((np.arange(330)%4, counts)).ravel().tolist()
            self.assertEqual(len(json.dumps(numeric, separators=(',', ':'))), length)
            packet = encode_cells(states)
            self.assertEqual(packet, original_encoder()(states))
            self.assertEqual(packet['encoding'], 'rle2-v1' if length <= 1370 else 'packed2-v1')
            np.testing.assert_array_equal(decode(packet), states)

    def test_noncontiguous_readonly_and_list_inputs_keep_existing_conversion(self):
        array = (np.arange(8192, dtype=np.int64).reshape(64,128) % 4)[:,::2]
        array.setflags(write=False)
        before = array.tobytes()
        original = original_encoder()
        for value in (array, array.tolist(), array.astype(np.int16), np.full(4096, 256, dtype=np.int64)):
            self.assertEqual(encode_cells(value), original(value))
        self.assertEqual(array.tobytes(), before)
        self.assertFalse(array.flags.writeable)

    def test_invalid_size_and_state_are_rejected(self):
        for value in ([], np.zeros(4095), np.zeros(4097), np.full(4096, 4), np.full(4096, 255)):
            with self.assertRaisesRegex(ValueError, '^invalid_tile_states$'):
                encode_cells(value)


if __name__ == '__main__':
    unittest.main()
