"""Synthetic NE containers assembled from small published instruction contexts."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import struct
from tests.support import temporary_directory
import unittest

from wasdify.cli import main
from wasdify.engine import build_result
from wasdify.models import WasdifyError
from wasdify.patches.kremlin_wasd import WasdControls, profiles
from wasdify.service import Service
from tests.test_kremlin_resolution import selector


def executable(language="ru", base=0x400):
    """No real executable, data segment, game assets, or full function dumps."""
    profile = next(p for p in profiles() if p["language"] == language)
    size = 0x5000
    starts = {name: base + i * size for i, name in enumerate(profile["anchors"])}
    indexes = {name: i + 1 for i, name in enumerate(profile["anchors"])}
    data = bytearray(base + size * 4 + 0x800)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 0x80)
    data[0x80:0x82] = b"NE"
    struct.pack_into("<H", data, 0x9c, 4)
    struct.pack_into("<H", data, 0xa2, 64)
    struct.pack_into("<H", data, 0xb2, 4)
    for name, anchor in profile["anchors"].items():
        start = starts[name]
        # Leave room for the relocation table before the next segment.
        length = 0x4e00 if name != "reader" else 0x1000
        struct.pack_into("<4H", data, 0xc0 + (indexes[name] - 1) * 8, start >> 4, length, 0x1d50, length)
        signature = bytes.fromhex(anchor["signature"])
        data[start + anchor["offset"]:start + anchor["offset"] + len(signature)] = signature
        for region in profile["regions"]:
            if region["anchor"] == name:
                offset = start + anchor["offset"] + region["relative"]
                old = bytes.fromhex(region["expected"])
                data[offset:offset + len(old)] = old
        records = []
        for source in profile["removed"].get(name, ()):
            target_offset = profile["reader_offset"]
            if name == "input" and source in (anchor["offset"] + 0x270, anchor["offset"] + 0x317):
                target_offset += 76
            records.append((3, 0, source, indexes["reader"], target_offset))
        if name == "input":
            records += [(3, 0, profile["retained"]["reader"], indexes["reader"], profile["reader_offset"]),
                        (3, 0, profile["retained"]["mouse_init"], indexes["mouse"], 0)]
            # Resolution patch context in the same synthetic file.
            data[start + 0x500:start + 0x564] = selector()
        if name == "reader":
            irq = bytes.fromhex(profile["irq_signature"])
            data[start + 0x100:start + 0x100 + len(irq)] = irq
        if name == "player":
            for context in profile["physics_callers"]:
                expected = bytes.fromhex(context["expected"])
                offset = start + context["offset"]
                data[offset:offset + len(expected)] = expected
        for kind, flags, source, target, off in records:
            data[start + source:start + source + 4] = b"\xff\xff\0\0"
        table = struct.pack("<H", len(records)) + b"".join(struct.pack("<BBHHH", *r) for r in records)
        data[start + length:start + length + len(table)] = table
    return bytes(data)


class WasdTests(unittest.TestCase):
    def test_upgrade_previous_sensitivity_and_reapply(self):
        for lang in ("ru", "en"):
            source = executable(lang)
            current = build_result({"main": source}, WasdControls().analyze({"main": source}).changes)["main"]
            profile = next(p for p in profiles() if p["language"] == lang)
            region = profile["regions"][4]
            for previous_bytes in region["previous_replacements"]:
                previous = current.replace(bytes.fromhex(region["replacement"]),
                                           bytes.fromhex(previous_bytes))
                self.assertNotEqual(current, previous)
                upgrade = WasdControls().analyze({"main": previous})
                self.assertEqual(len(upgrade.changes), 1)
                self.assertFalse(upgrade.already_applied)
                self.assertEqual(build_result({"main": previous}, upgrade.changes)["main"], current)
            self.assertTrue(WasdControls().analyze({"main": current}).already_applied)

    def test_both_profiles_relocated_segments_and_idempotence(self):
        for lang in ("ru", "en"):
            for base in (0x400, 0x900):
                with self.subTest(language=lang, base=base):
                    data = executable(lang, base)
                    analysis = WasdControls().analyze({"main": data})
                    self.assertEqual(len(analysis.changes), 10)
                    out = build_result({"main": data}, analysis.changes)["main"]
                    self.assertEqual(len(out), len(data))
                    self.assertTrue(WasdControls().analyze({"main": out}).already_applied)

    def test_partial_and_damaged_patch_are_rejected(self):
        data = executable()
        analysis = WasdControls().analyze({"main": data})
        for change in analysis.changes:
            partial = build_result({"main": data}, (change,))["main"]
            with self.subTest(offset=change.offset), self.assertRaises(WasdifyError):
                WasdControls().analyze({"main": partial})
        damaged = bytearray(data)
        damaged[analysis.changes[0].offset + 1] ^= 1
        with self.assertRaises(WasdifyError):
            WasdControls().analyze({"main": bytes(damaged)})

    def test_upgrade_complete_legacy_mouse_keeps_all_keyboard_regions(self):
        for language in ("ru", "en"):
            source = executable(language)
            current = build_result({"main": source}, WasdControls().analyze({"main": source}).changes)["main"]
            profile = next(p for p in profiles() if p["language"] == language)
            legacy = current
            for index in (4, 6, 7):
                region = profile["regions"][index]
                legacy = legacy.replace(bytes.fromhex(region["replacement"]),
                                        bytes.fromhex(region["previous_replacements"][-1]), 1)
            upgrade = WasdControls().analyze({"main": legacy})
            self.assertEqual(len(upgrade.changes), 3)
            self.assertEqual(build_result({"main": legacy}, upgrade.changes)["main"], current)

    def test_missing_and_ambiguous_context(self):
        data = bytearray(executable())
        signature = bytes.fromhex(profiles()[0]["anchors"]["input"]["signature"])
        for replacement in (b"X" * len(signature), signature):
            candidate = bytearray(data)
            if replacement == signature:
                candidate[0x400 + 0x700:0x400 + 0x700 + len(signature)] = signature
            else:
                offset = candidate.find(signature)
                candidate[offset:offset + len(signature)] = replacement
            with self.assertRaises(WasdifyError):
                WasdControls().analyze({"main": bytes(candidate)})

    def test_data_segments_overlay_and_malformed_container(self):
        for flags in (0x1d51, 0x3d50):
            data = bytearray(executable())
            struct.pack_into("<H", data, 0xc4, flags)
            with self.assertRaises(WasdifyError):
                WasdControls().analyze({"main": bytes(data)})
        for data in (b"MZ", executable()[:0x100], executable().replace(b"NE", b"XX", 1)):
            with self.assertRaises(WasdifyError):
                WasdControls().analyze({"main": data})
        # Overlay copies of anchors cannot satisfy a missing code context.
        data = executable()
        signature = bytes.fromhex(profiles()[0]["anchors"]["input"]["signature"])
        with self.assertRaises(WasdifyError):
            WasdControls().analyze({"main": data.replace(signature, b"X" * len(signature)) + signature})

    def test_loader_fixup_cannot_overwrite_payload(self):
        data = bytearray(executable())
        table = 0x400 + 0x4e00
        count = struct.unpack_from("<H", data, table)[0]
        struct.pack_into("<H", data, table, count + 1)
        struct.pack_into("<BBHHH", data, table + 2 + count * 8, 3, 0, 0x1651, 4, 0)
        with self.assertRaises(WasdifyError):
            WasdControls().analyze({"main": bytes(data)})

    def test_physics_caller_contexts_are_required(self):
        for language in ("ru", "en"):
            profile = next(p for p in profiles() if p["language"] == language)
            for context in profile["physics_callers"]:
                data = bytearray(executable(language))
                offset = data.find(bytes.fromhex(context["expected"]))
                self.assertGreaterEqual(offset, 0)
                data[offset + 5] ^= 1
                with self.assertRaises(WasdifyError):
                    WasdControls().analyze({"main": bytes(data)})

    def test_service_backup_cli_and_resolution_in_any_order(self):
        for lang in ("ru", "en"):
            for sequence in ((("wasd", "resolution-640x400"),),
                             (("wasd",), ("resolution-640x400",)),
                             (("resolution-640x400",), ("wasd",))):
                with temporary_directory() as folder:
                    target = Path(folder) / "renamed.bin"
                    data = executable(lang)
                    target.write_bytes(data)
                    service = Service()
                    for selected in sequence:
                        plan = service.plan({"main": target}, selected)
                        self.assertTrue(plan.ready, plan.issues)
                        before = target.read_bytes()
                        result = service.apply(plan)
                        self.assertEqual(result.backup.read_bytes(), before)
                    out = target.read_bytes()
                    again = service.plan({"main": target}, ("wasd", "resolution-640x400"))
                    self.assertTrue(again.ready, again.issues)
                    self.assertEqual(service.apply(again).status, "no_changes")
                    self.assertEqual(target.read_bytes(), out)
                    with redirect_stdout(StringIO()) as stream:
                        code = main(["apply", str(target), "--patch", "wasd", "--json"])
                    self.assertEqual(code, 0, stream.getvalue())


if __name__ == "__main__":
    unittest.main()
