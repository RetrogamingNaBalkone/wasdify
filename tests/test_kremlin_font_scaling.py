"""Synthetic sparse NE containers, no game files or font assets."""
from pathlib import Path
import struct
import unittest

from tests.support import temporary_directory
from tests.test_kremlin_resolution import selector
from wasdify.engine import build_result
from wasdify.models import WasdifyError
from wasdify.patches.kremlin_font_scaling import FontScaling, profiles
from wasdify.service import Service


def executable(language="ru", base=0x1000):
    p = next(p for p in profiles() if p["language"] == language)
    starts = {"text": base, "startup": base+0x4000, "data": base+0x8000}
    data = bytearray(base+0xc000)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 0x80)
    data[0x80:0x82] = b"NE"
    struct.pack_into("<H", data, 0x9c, p["data_index"])
    struct.pack_into("<H", data, 0xa2, 64)
    struct.pack_into("<H", data, 0xb2, 4)
    for role, start in starts.items():
        struct.pack_into("<4H", data, 0xc0+(p[role+"_index"]-1)*8,
                         start >> 4, 0x3000, 0x1d51 if role == "data" else 0x1d50, 0x3000)
        struct.pack_into("<H", data, start+0x3000, 0)
    for anchor in p["anchors"]:
        content = bytes(int(b, 16) if b != "??" else 0 for b in anchor["signature"].split())
        at = starts[anchor["role"]]+anchor["offset"]
        data[at:at+len(content)] = content
    for region in p["regions"]:
        content = bytes.fromhex(region["expected"])
        at = starts[region["role"]]+region["offset"]
        data[at:at+len(content)] = content
    for role in ("text", "startup"):
        fixups = [f for f in p["fixups"] if f["role"] == role]
        struct.pack_into("<H", data, starts[role]+0x3000, len(fixups))
        for i, f in enumerate(fixups):
            struct.pack_into("<BBHHH", data, starts[role]+0x3002+i*8, *f["record"])
    data[starts["startup"]+0x2000:starts["startup"]+0x2064] = selector()
    return bytes(data)


class FontScalingTests(unittest.TestCase):
    def test_versions_shifted_segments_and_repeat(self):
        for lang in ("ru", "en"):
            for base in (0x1000, 0x3000):
                data = executable(lang, base)
                a = FontScaling().analyze({"main": data})
                self.assertEqual(len(a.changes), 3)
                result = build_result({"main": data}, a.changes)["main"]
                self.assertEqual(len(result), len(data))
                self.assertTrue(FontScaling().analyze({"main": result}).already_applied)

    def test_corruption_partial_and_ambiguous(self):
        for lang in ("ru", "en"):
            source = executable(lang)
            a = FontScaling().analyze({"main": source})
            result = build_result({"main": source}, a.changes)["main"]
            for image in (source, result):
                for change in a.changes:
                    data = bytearray(image)
                    data[change.offset] ^= 1
                    with self.assertRaises(WasdifyError):
                        FontScaling().analyze({"main": bytes(data)})
            for mask in range(1, 7):
                partial = build_result({"main": source}, tuple(
                    c for i, c in enumerate(a.changes) if mask & (1 << i)))["main"]
                with self.assertRaises(WasdifyError):
                    FontScaling().analyze({"main": partial})
            p = next(p for p in profiles() if p["language"] == lang)
            for anchor in p["anchors"]:
                data = bytearray(source)
                base = 0x9000 if anchor["role"] == "data" else 0x5000 if anchor["role"] == "startup" else 0x1000
                data[base+anchor["offset"]] ^= 1
                with self.assertRaises(WasdifyError):
                    FontScaling().analyze({"main": bytes(data)})
            data = bytearray(source)
            content = bytes.fromhex(p["anchors"][0]["signature"])
            data[0x3800:0x3800+len(content)] = content
            with self.assertRaises(WasdifyError):
                FontScaling().analyze({"main": bytes(data)})

    def test_segment_types_and_relocations(self):
        for lang in ("ru", "en"):
            p = next(p for p in profiles() if p["language"] == lang)
            for mode in ("data", "32bit", "overlay", "fixup", "write", "entry", "cycle", "range"):
                data = bytearray(executable(lang))
                entry = 0xc0+(p["text_index"]-1)*8
                if mode in ("data", "32bit"):
                    struct.pack_into("<H", data, entry+4, 0x1d51 if mode == "data" else 0x3d50)
                elif mode == "overlay":
                    struct.pack_into("<H", data, entry+2, 0x100)
                elif mode == "fixup":
                    data[0x8002] ^= 1
                else:
                    target = p["regions"][0]["offset"]
                    records = {"write": (5, 4, target+1, 1, 0),
                               "entry": (3, 4, 0x2800, p["text_index"], target+1),
                               "cycle": (5, 0, 0x2800, 1, 0),
                               "range": (5, 0, 0x2fff, 1, 0)}
                    count = struct.unpack_from("<H", data, 0x4000)[0]
                    struct.pack_into("<H", data, 0x4000, count+1)
                    struct.pack_into("<BBHHH", data, 0x4002+count*8, *records[mode])
                    struct.pack_into("<H", data, 0x3800, 0x2800)
                with self.assertRaises(WasdifyError, msg=(lang, mode)):
                    FontScaling().analyze({"main": bytes(data)})
            for role, base in (("text", 0x4000), ("startup", 0x8000)):
                for slot in range(sum(f["role"] == role for f in p["fixups"])):
                    data = bytearray(executable(lang))
                    data[base+2+slot*8+6] ^= 1
                    with self.assertRaises(WasdifyError):
                        FontScaling().analyze({"main": bytes(data)})

    def test_service_backup_and_independent_resolution(self):
        for lang in ("ru", "en"):
            with temporary_directory() as directory:
                path = Path(directory) / "renamed.exe"
                source = executable(lang)
                service = Service()
                for order in (("font-scaling", "resolution-640x400"),
                              ("resolution-640x400", "font-scaling")):
                    path.write_bytes(source)
                    for patch in order:
                        plan = service.plan({"main": path}, (patch,))
                        self.assertTrue(plan.ready, plan.issues)
                        result = service.apply(plan)
                        if patch == order[0]:
                            self.assertEqual(result.backup.read_bytes(), source)
                    self.assertEqual(service.apply(service.plan({"main": path}, order)).status, "no_changes")


if __name__ == "__main__":
    unittest.main()
