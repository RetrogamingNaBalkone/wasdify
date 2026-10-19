"""Synthetic containers and published small instruction contexts, no game files."""
from pathlib import Path
import struct
import tempfile
import unittest

from wasdify.engine import build_result
from wasdify.models import WasdifyError
from wasdify.patches.kremlin_numeric_hud import NumericHud, profiles
from wasdify.service import Service
from tests.test_kremlin_render import executable as render_executable


def executable(language="ru", base=0x18000):
    data = bytearray(render_executable(language))
    data.extend(bytes(base + 0x9000 - len(data)))
    struct.pack_into("<H", data, 0x9c, 8)
    struct.pack_into("<4H", data, 0xe8, base >> 4, 0x3000, 0x1d50, 0x3000)
    struct.pack_into("<4H", data, 0xf0, (base + 0x4000) >> 4, 0x2000, 0x1d50, 0x2000)
    struct.pack_into("<4H", data, 0xf8, (base + 0x7000) >> 4, 0x1000, 0x1d50, 0x1000)
    p = next(p for p in profiles() if p["language"] == language)
    offset = base + p["offset"]
    data[offset:offset + 330] = bytes.fromhex(p["expected"])
    for anchor in p["anchors"]:
        content = bytes.fromhex(anchor["expected"])
        offset = base + anchor["offset"]
        data[offset:offset + len(content)] = content
    for region in p["regions"]:
        content = bytes.fromhex(region["expected"])
        offset = base + region["offset"]
        data[offset:offset + len(content)] = content
    content = bytes.fromhex(p["text_signature"])
    offset = base + 0x4000 + p["text_offset"]
    data[offset:offset + len(content)] = content
    records = [tuple(r) for r in p["fixups"]]
    records.append((3, 0, p["print_source"], 7, p["print_target"]))
    struct.pack_into("<H", data, base + 0x3000, len(records))
    for i, r in enumerate(records):
        struct.pack_into("<BBHHH", data, base + 0x3002 + i*8, *r)
    struct.pack_into("<H", data, base + 0x6000, 0)
    pixel=p["pixel"]
    content=bytes.fromhex(pixel["signature"])
    start=base+0x7000+pixel["offset"]
    data[start:start+len(content)]=content
    struct.pack_into("<HBBHHH",data,base+0x8000,1,*pixel["fixup"])
    return bytes(data)


class NumericHudTests(unittest.TestCase):
    def test_both_versions_shifted_segments_and_repeat(self):
        for language in ("ru", "en"):
            for base in (0x18000, 0x1a000):
                data = executable(language, base)
                analysis = NumericHud().analyze({"main": data})
                self.assertEqual(len(analysis.changes), 7)
                result = build_result({"main": data}, analysis.changes)["main"]
                self.assertEqual(len(data), len(result))
                self.assertTrue(NumericHud().analyze({"main": result}).already_applied)
                change = analysis.changes[0]
                self.assertEqual(change.expected[0x62:0x85], change.replacement[0x62:0x85])

    def test_corruption_in_each_context_and_payload(self):
        for lang in ("ru", "en"):
            profile = next(p for p in profiles() if p["language"] == lang)
            source = executable(lang)
            patched = build_result({"main": source}, NumericHud().analyze({"main": source}).changes)["main"]
            offsets = [profile["offset"], profile["offset"] + 200,
                       *(a["offset"] + 5 for a in profile["anchors"]),
                       *(r["offset"] + 5 for r in profile["regions"]),
                       0x4000 + profile["text_offset"]]
            for image in (source, patched):
                for offset in offsets:
                    data = bytearray(image)
                    data[0x18000 + offset] ^= 1
                    with self.assertRaises(WasdifyError):
                        NumericHud().analyze({"main": bytes(data)})

    def test_data_32bit_overlay_missing_and_duplicate_context(self):
        for kind in ("data", "32bit", "overlay", "duplicate", "duplicate-anchor",
                     "missing-text", "wrong-text-target", "missing-pixel", "wrong-pixel-fixup"):
            data = bytearray(executable())
            if kind in ("data", "32bit"):
                struct.pack_into("<H", data, 0xec, 0x1d51 if kind == "data" else 0x3d50)
            elif kind == "overlay":
                struct.pack_into("<H", data, 0xea, 0x100)
            elif kind == "duplicate":
                data.extend(bytes(0x26000 - len(data)))
                struct.pack_into("<H", data, 0x9c, 9)
                struct.pack_into("<4H", data, 0x100, 0x2200, 0x3000, 0x1d50, 0x3000)
                data[0x22000:0x2503a] = data[0x18000:0x1b03a]
            elif kind == "missing-text":
                data[0x1c338] ^= 1
            elif kind == "missing-pixel":
                data[0x1f65c] ^= 1
            elif kind == "wrong-pixel-fixup":
                struct.pack_into("<H", data, 0x20008, 112)
            elif kind == "duplicate-anchor":
                p = profiles()[0]
                anchor = bytes.fromhex(p["anchors"][0]["expected"])
                data[0x1a500:0x1a500 + len(anchor)] = anchor
            else:
                struct.pack_into("<H", data, 0x1b002 + len(profiles()[0]["fixups"])*8 + 4, 5)
            with self.assertRaises(WasdifyError):
                NumericHud().analyze({"main": bytes(data)})

    def test_fixup_writes_chains_and_interior_entry(self):
        for record in ((5, 4, 0xa42, 1, 0), (3, 0, 0x2000, 6, 0xa37),
                       (5, 0, 0x2000, 1, 0), (5, 0, 0x2fff, 1, 0),
                       (3, 4, 0xa98, 1, 0)):
            data = bytearray(executable())
            count = len(profiles()[0]["fixups"]) + 1
            struct.pack_into("<H", data, 0x1b000, count+1)
            struct.pack_into("<BBHHH", data, 0x1b002+count*8, *record)
            struct.pack_into("<H", data, 0x1a000, 0x2000)
            with self.assertRaises(WasdifyError):
                NumericHud().analyze({"main": bytes(data)})
        for slot in range(len(profiles()[0]["fixups"]) + 1):
            data = bytearray(executable())
            data[0x1b002 + slot*8] ^= 1
            with self.assertRaises(WasdifyError):
                NumericHud().analyze({"main": bytes(data)})

    def test_upgrade_v1_and_reject_partial_v2(self):
        for lang in ("ru", "en"):
            source = executable(lang)
            profile = next(p for p in profiles() if p["language"] == lang)
            previous = bytearray(source)
            offset = 0x18000 + profile["offset"]
            previous[offset:offset+330] = bytes.fromhex(profile["previous_replacement"])
            upgrade = NumericHud().analyze({"main": bytes(previous)})
            self.assertEqual(len(upgrade.changes), 7)
            current = build_result({"main": bytes(previous)}, upgrade.changes)["main"]
            self.assertTrue(NumericHud().analyze({"main": current}).already_applied)
            for change in upgrade.changes:
                partial = build_result({"main": bytes(previous)}, (change,))["main"]
                with self.assertRaises(WasdifyError):
                    NumericHud().analyze({"main": partial})

    def test_service_backup_all_patches_and_both_orders(self):
        selected = ("numeric-hud", "wasd", "resolution-640x400", "render-optimization")
        for lang in ("ru", "en"):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "renamed.exe"
                source = executable(lang)
                path.write_bytes(source)
                service = Service()
                plan = service.plan({"main": path}, selected)
                self.assertTrue(plan.ready, plan.issues)
                service.apply(plan)
                self.assertEqual(next(Path(directory).glob("*.bak")).read_bytes(), source)
                self.assertEqual(service.apply(service.plan({"main": path}, selected)).status, "no_changes")
                for order in (selected, tuple(reversed(selected))):
                    path.write_bytes(source)
                    for patch in order:
                        plan = service.plan({"main": path}, (patch,))
                        self.assertTrue(plan.ready, plan.issues)
                        service.apply(plan)
                    self.assertEqual(service.apply(service.plan({"main": path}, selected)).status, "no_changes")


if __name__ == "__main__":
    unittest.main()
