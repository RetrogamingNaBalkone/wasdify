"""Synthetic NE and instruction contexts; no original executables."""
import struct
import unittest

from tests.test_kremlin_resolution import executable
from wasdify.engine import build_result
from wasdify.models import WasdifyError
from wasdify.service import Service
from tests.support import temporary_directory
from wasdify.patches.kremlin_weapons import (
    WeaponSelectionFix, CONTEXT, BEGIN, END, ORIGINAL, REPLACEMENT, EPILOGUE,
)


def fixture(base=0x200):
    data = bytearray(executable(base=base)+bytes(16))
    data[base+0x100:base+0x100+len(CONTEXT)] = CONTEXT
    data[base+0x1ec:base+0x1f2] = EPILOGUE
    return bytes(data)


class WeaponTests(unittest.TestCase):
    def test_service_apply_backup_and_repeat(self):
        with temporary_directory() as directory:
            path = directory / 'game.exe'
            original = fixture()
            path.write_bytes(original)
            service = Service()
            selected = ('weapon-selection-fix',)
            plan = service.plan({'main': path}, selected)
            self.assertTrue(plan.ready, plan.issues)
            result = service.apply(plan)
            self.assertEqual(result.status, 'applied')
            self.assertTrue(any(p.read_bytes() == original for p in directory.glob('*.bak')))
            repeat = service.plan({'main': path}, selected)
            self.assertTrue(repeat.ready, repeat.issues)
            self.assertEqual(service.apply(repeat).status, 'no_changes')

    def test_shifted_context_and_idempotence(self):
        for base in (0x200, 0x900):
            original = fixture(base)
            analysis = WeaponSelectionFix().analyze({'main': original})
            change, = analysis.changes
            self.assertEqual((change.offset, change.expected, change.replacement),
                             (base+0x100+BEGIN, ORIGINAL, REPLACEMENT))
            patched = build_result({'main': original}, analysis.changes)['main']
            self.assertEqual(len(original), len(patched))
            self.assertTrue(WeaponSelectionFix().analyze({'main': patched}).already_applied)

    def test_wrong_partial_or_ambiguous_context(self):
        for index in (0x300, 0x300+BEGIN, 0x300+END, 0x3ec):
            data = bytearray(fixture());data[index] ^= 1
            with self.subTest(index=index), self.assertRaises(WasdifyError):
                WeaponSelectionFix().analyze({'main': bytes(data)})
        data = bytearray(fixture())
        data[0x400:0x400+len(CONTEXT)] = CONTEXT
        with self.assertRaises(WasdifyError):
            WeaponSelectionFix().analyze({'main': bytes(data)})

    def test_data_and_overlay_do_not_establish_compatibility(self):
        data = bytearray(fixture())
        struct.pack_into('<H', data, 0xc4, 0x1d51)
        with self.assertRaises(WasdifyError):
            WeaponSelectionFix().analyze({'main': bytes(data)})
        data = executable()+CONTEXT+bytes(0xec-len(CONTEXT))+EPILOGUE
        with self.assertRaises(WasdifyError):
            WeaponSelectionFix().analyze({'main': data})

    def test_loader_fixup_and_chain_cannot_touch_edit(self):
        for chained in (False, True):
            data = bytearray(fixture())
            source = 0x100+BEGIN if not chained else 0x350
            struct.pack_into('<HBBHHH', data, 0x600, 1, 3, 0, source, 1, 0)
            if chained:
                struct.pack_into('<H', data, 0x550, 0x100+BEGIN)
            with self.subTest(chained=chained), self.assertRaises(WasdifyError):
                WeaponSelectionFix().analyze({'main': bytes(data)})
