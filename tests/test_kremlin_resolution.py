"""Synthetic NE containers only; never load game files in the test suite."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import struct
import tempfile
import unittest

from wasdify.cli import main
from wasdify.engine import build_result
from wasdify.models import WasdifyError
from wasdify.patches.kremlin_resolution import Resolution640x400, locate, probe
from wasdify.service import Service

GAME = "dungeons-of-kremlin"
PATCH = "resolution-640x400"


def selector(first=1):
    # Assemble four synthetic branches with the researched instruction layout.
    result = bytearray()
    for index, mode in enumerate((0x100 + first, 0x101, 0x103, 0x105)):
        result.extend(bytes.fromhex("83 7E 06") + bytes([index + 1]))
        result.extend(bytes.fromhex("75 13 0E E8"))
        result.extend(struct.pack("<h", -527 - 25 * index))
        result.extend(bytes.fromhex("0B C0 74 0B 68"))
        result.extend(struct.pack("<H", mode))
        result.extend(bytes.fromhex("0E E8"))
        result.extend(struct.pack("<h", 1498 - 25 * index))
        result.extend(bytes.fromhex("90 83 C4 02"))
    return bytes(result)


def executable(first=1, *, base=0x200, duplicate=False, flags=0x1D50):
    data = bytearray(base + 0x400)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x82] = b"NE"
    struct.pack_into("<H", data, 0x80 + 0x1C, 1)
    struct.pack_into("<H", data, 0x80 + 0x22, 64)
    struct.pack_into("<H", data, 0x80 + 0x32, 4)
    struct.pack_into("<4H", data, 0xC0, base >> 4, 0x400, flags, 0x400)
    data[base + 0x20:base + 0x84] = selector(first)
    if duplicate:
        data[base + 0x180:base + 0x1E4] = selector(0)
    return bytes(data)


class ResolutionTests(unittest.TestCase):
    def test_single_byte_change_at_relocated_segment(self):
        for base in (0x200, 0x900):
            with self.subTest(base=base):
                data = executable(base=base)
                analysis = Resolution640x400().analyze({"main": data})
                self.assertEqual(len(analysis.changes), 1)
                change = analysis.changes[0]
                self.assertEqual((change.offset, change.expected, change.replacement),
                                 (base + 0x2F, b"\x01", b"\x00"))
                result = build_result({"main": data}, analysis.changes)["main"]
                self.assertEqual(result, executable(0, base=base))
                self.assertEqual(len(data), len(result))
                self.assertTrue(probe({"main": result}))
                self.assertTrue(Resolution640x400().analyze({"main": result}).already_applied)

    def test_absent_damaged_and_ambiguous_signature(self):
        damaged = bytearray(executable())
        damaged[0x220 + 40] ^= 1  # Even the unchanged -r2 branch is required.
        for data in (executable().replace(selector(), b"X" * 100), bytes(damaged), executable(duplicate=True)):
            with self.subTest(data=data[0x220:0x230]):
                self.assertFalse(probe({"main": data}))
                with self.assertRaises(WasdifyError) as error:
                    locate(data)
                self.assertEqual(error.exception.issue.code, "signature_count")

    def test_mixed_original_and_patched_matches_are_ambiguous(self):
        with self.assertRaises(WasdifyError) as error:
            locate(executable(duplicate=True))
        self.assertEqual(error.exception.issue.params["actual"], 2)

    def test_other_immediate_is_not_treated_as_already_applied(self):
        with self.assertRaises(WasdifyError) as error:
            locate(executable(2))
        self.assertEqual(error.exception.issue.code, "unexpected_bytes")
        self.assertFalse(probe({"main": executable(2)}))

    def test_data_32bit_segments_and_overlay_are_not_code(self):
        for flags in (0x1D51, 0x3D50):
            self.assertFalse(probe({"main": executable(flags=flags)}))
        data = executable().replace(selector(), b"X" * 100) + selector()
        self.assertFalse(probe({"main": data}))
        # An overlay decoy cannot make a unique code match ambiguous.
        self.assertEqual(locate(executable() + selector()), 0x22F)

    def test_container_boundaries_and_malformed_headers(self):
        cases = [b"", b"MZ" + selector(), executable()[:-1]]
        for offset, fmt, value in ((0x3C, "I", 0xFFFFFFF0), (0x9C, "H", 0xFFFF),
                                   (0xA2, "H", 2), (0xB2, "H", 17),
                                   (0xC0, "H", 1), (0xC2, "H", 0xFFFF)):
            data = bytearray(executable())
            struct.pack_into("<" + fmt, data, offset, value)
            cases.append(bytes(data))
        for data in cases:
            self.assertFalse(probe({"main": data}))
            with self.assertRaises(WasdifyError):
                locate(data)

    def test_signature_cannot_cross_segment_boundary(self):
        data = bytearray(executable())
        struct.pack_into("<H", data, 0xC2, 0x40)
        self.assertFalse(probe({"main": bytes(data)}))

    def test_overlapping_segments_are_rejected(self):
        data = bytearray(executable())
        struct.pack_into("<H", data, 0x9C, 2)
        data[0xC8:0xD0] = data[0xC0:0xC8]
        self.assertFalse(probe({"main": bytes(data)}))

    def test_second_code_segment_match_is_ambiguous(self):
        data = bytearray(executable())
        data.extend(b"\x00" * 0x400)
        struct.pack_into("<H", data, 0x9C, 2)
        struct.pack_into("<4H", data, 0xC8, 0x60, 0x400, 0x1D50, 0x400)
        data[0x620:0x684] = selector()
        with self.assertRaises(WasdifyError) as error:
            locate(bytes(data))
        self.assertEqual(error.exception.issue.params["actual"], 2)

    def test_service_detect_apply_backup_and_idempotence(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "renamed.bin"
            target.write_bytes(executable())
            service = Service()
            self.assertEqual(service.detect(target).method, "signature")
            plan = service.plan({"main": target}, (PATCH,))
            self.assertTrue(plan.ready, plan.issues)
            result = service.apply(plan)
            self.assertEqual(result.status, "applied")
            self.assertEqual(result.backup.read_bytes(), executable())
            self.assertEqual(target.read_bytes(), executable(0))
            again = service.plan({"main": target}, (PATCH,))
            self.assertEqual(again.already_applied, (PATCH,))
            self.assertEqual(service.apply(again).status, "no_changes")
            self.assertEqual(len(list(Path(folder).glob("*.bak"))), 1)

    def test_manual_game_does_not_bypass_context_and_stub_blocks_set(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "PKR.EXE"
            target.write_bytes(executable(2))
            service = Service()
            plan = service.plan({"main": target}, (PATCH,), GAME)
            self.assertFalse(plan.ready)
            with self.assertRaises(WasdifyError):
                service.apply(plan)
            target.write_bytes(executable())
            plan = service.plan({"main": target}, (PATCH, "wasd"))
            self.assertFalse(plan.ready)
            with self.assertRaises(WasdifyError):
                service.apply(plan)
            self.assertEqual(target.read_bytes(), executable())
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_actual_cli_registration_and_json(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "renamed.bin"
            target.write_bytes(executable())
            for operation in ("plan", "apply", "apply"):
                stream = StringIO()
                with redirect_stdout(stream):
                    code = main([operation, str(target), "--patch", PATCH, "--json"])
                self.assertEqual(code, 0, stream.getvalue())
                value = json.loads(stream.getvalue())
                if operation == "plan":
                    self.assertEqual(value["plan"]["changes"][0]["expected"], "01")
                    self.assertEqual(value["plan"]["changes"][0]["replacement"], "00")
            self.assertEqual(target.read_bytes(), executable(0))


if __name__ == "__main__":
    unittest.main()
