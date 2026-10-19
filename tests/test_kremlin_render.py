"""Synthetic NE contexts only; no original executable or game assets."""
from pathlib import Path
import struct
from tests.support import temporary_directory
import unittest

from wasdify.engine import build_result
from wasdify.models import WasdifyError
from wasdify.patches.kremlin_render import RenderOptimization, profiles
from wasdify.service import Service
from tests.test_kremlin_wasd import executable as wasd_executable


def function(profile):
    data = bytearray(int(b, 16) if b != "??" else 0 for b in profile["signature"].split())
    for region in profile["regions"]:
        old = bytes.fromhex(region["expected"])
        data[region["relative"]:region["relative"] + len(old)] = old
    return bytes(data)


def executable(language="ru", base=0x16000):
    data = bytearray(wasd_executable(language))
    data.extend(bytes(max(0, base + 0x800 - len(data))))
    struct.pack_into("<H", data, 0x9c, 5)
    struct.pack_into("<4H", data, 0xe0, base >> 4, 0x400, 0x1d50, 0x400)
    code = function(next(p for p in profiles() if p["language"] == language))
    data[base:base + len(code)] = code
    # Genuine loader fixup shape, but synthetic container and other segments.
    struct.pack_into("<HBBHHH", data, base + 0x400, 1, 5, 1, 0x22, 1, 0x71)
    return bytes(data)


class RenderTests(unittest.TestCase):
    def test_profiles_shifted_segments_and_repeat(self):
        for language in ("ru", "en"):
            for base in (0x16000, 0x18000):
                data = executable(language, base)
                analysis = RenderOptimization().analyze({"main": data})
                self.assertEqual(len(analysis.changes), 3)
                result = build_result({"main": data}, analysis.changes)["main"]
                self.assertEqual(len(result), len(data))
                self.assertTrue(RenderOptimization().analyze({"main": result}).already_applied)
                self.assertEqual(sum(a != b for a, b in zip(data, result)), 9)

    def test_damage_and_partial_states(self):
        source = executable()
        changes = RenderOptimization().analyze({"main": source}).changes
        for mask in range(1, 7):
            partial = build_result({"main": source}, tuple(c for i, c in enumerate(changes) if mask & (1 << i)))["main"]
            with self.assertRaises(WasdifyError):
                RenderOptimization().analyze({"main": partial})
        for relative in (0, 0x8f, 0x90, 0x97, 0x9f, 0xb0, 0xc8):
            damaged = bytearray(source)
            damaged[0x16000 + relative] ^= 1
            with self.assertRaises(WasdifyError):
                RenderOptimization().analyze({"main": bytes(damaged)})

    def test_ambiguous_data_overlay_and_nonzero_entry(self):
        source = executable()
        for kind in ("duplicate", "data", "32bit", "overlay", "shift"):
            data = bytearray(source)
            if kind == "duplicate":
                data[0x16200:0x162d2] = data[0x16000:0x160d2]
            elif kind in ("data", "32bit"):
                struct.pack_into("<H", data, 0xe4, 0x1d51 if kind == "data" else 0x3d50)
            elif kind == "overlay":
                struct.pack_into("<H", data, 0xe2, 0x80)
            else:
                data[0x16010:0x160e2] = data[0x16000:0x160d2]
                data[0x16000:0x16010] = bytes(16)
            with self.assertRaises(WasdifyError):
                RenderOptimization().analyze({"main": bytes(data)})

    def test_relocation_damage_chains_and_interior_entry(self):
        for source, flags, target, offset in ((0x90, 4, 1, 0), (0x300, 0, 1, 0),
                                            (0x3ff, 0, 1, 0), (0x300, 4, 5, 0x90)):
            data = bytearray(executable())
            struct.pack_into("<H", data, 0x16400, 2)
            struct.pack_into("<BBHHH", data, 0x1640a, 5, flags, source, target, offset)
            struct.pack_into("<H", data, 0x16300, 0x300)  # cyclic source chain
            with self.assertRaises(WasdifyError):
                RenderOptimization().analyze({"main": bytes(data)})
        data = bytearray(executable())
        struct.pack_into("<H", data, 0x16400, 2)
        struct.pack_into("<BBHHH", data, 0x1640a, 5, 0, 0x300, 1, 0)
        struct.pack_into("<H", data, 0x16300, 0xb0)
        with self.assertRaises(WasdifyError):
            RenderOptimization().analyze({"main": bytes(data)})

    def test_required_loader_import(self):
        data = bytearray(executable())
        struct.pack_into("<H", data, 0x16400, 0)
        with self.assertRaises(WasdifyError):
            RenderOptimization().analyze({"main": bytes(data)})

    def test_service_all_patches_backup_and_independence(self):
        for language in ("ru", "en"):
            with temporary_directory() as directory:
                path = Path(directory) / "renamed.bin"
                source = executable(language)
                path.write_bytes(source)
                service = Service()
                selected = ("wasd", "resolution-640x400", "render-optimization")
                plan = service.plan({"main": path}, selected)
                self.assertTrue(plan.ready, plan.issues)
                service.apply(plan)
                self.assertEqual(next(Path(directory).glob("*.bak")).read_bytes(), source)
                self.assertTrue(service.plan({"main": path}, selected).ready)
                self.assertEqual(service.apply(service.plan({"main": path}, selected)).status, "no_changes")
                path.write_bytes(source)
                for patch in reversed(selected):
                    plan = service.plan({"main": path}, (patch,))
                    self.assertTrue(plan.ready, plan.issues)
                    service.apply(plan)


if __name__ == "__main__":
    unittest.main()
