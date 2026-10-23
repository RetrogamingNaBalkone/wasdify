"""Synthetic sparse NE contexts; never use reference EXEs as test fixtures."""
import json
from pathlib import Path
import struct
import unittest
from contextlib import redirect_stdout
from io import StringIO

from tests.support import temporary_directory
from tests.test_kremlin_settings import executable as settings_fixture
from wasdify.engine import build_result
from wasdify.models import WasdifyError
from wasdify.patches import kremlin_save_native as saves
from wasdify.patches import kremlin_settings as settings
from wasdify.patches.kremlin_wasd import segments
from wasdify.patches.kremlin_weapons import CONTEXT, EPILOGUE, timer_contexts
from wasdify.service import Service

SAVE = ('weapon-selection-fix','save-load')
SETTINGS = ('wasd','resolution-640x400','game-settings')


def executable(language='ru'):
    data = bytearray(settings_fixture(language))
    ne = struct.unpack_from('<I',data,60)[0]
    starts = {s.index:s.start for s in segments(data)}
    required = saves.resources()['bindings'][language].values()
    contexts = list(required)+[(39 if language=='ru' else 16,
                               0x239 if language=='ru' else 0x18cf,
                               '8cd09045558bec1e8ed85657c45e0626833f00'),
                              (39 if language=='ru' else 16,
                               0x15d if language=='ru' else 0x17f3,
                               '8cd09045558bec1e8ed883ec025657fa')]
    for index,offset,expected in contexts:
        if index not in starts:
            data.extend(bytes(-len(data)%512))
            start = starts[index] = len(data)
            data.extend(bytes(0x5200+2))
            struct.pack_into('<4H',data,ne+64+(index-1)*8,start>>9,0x5200,0x1d50,0x5200)
        start = starts[index]+offset
        data[start:start+len(bytes.fromhex(expected))] = bytes.fromhex(expected)
    for offset,body in ((0x1000,CONTEXT),(0x10ec,EPILOGUE),
                        (0x1400,timer_contexts()[int(language=='en')])):
        data[starts[1]+offset:starts[1]+offset+len(body)] = body
    return bytes(data)


class SavePatchTests(unittest.TestCase):
    def test_cli_and_service_produce_identical_bytes(self):
        from wasdify.cli import main
        with temporary_directory() as root:
            raw=executable('en'); path=root/'renamed.exe';path.write_bytes(raw)
            service=Service();chosen=SAVE+SETTINGS
            plan=service.plan({'main':path},chosen)
            expected=build_result({'main':raw},plan.changes)['main']
            with redirect_stdout(StringIO()) as output:
                result=main(['apply',str(path),'--lang','ru','--json',
                             *[arg for p in chosen for arg in ('--patch',p)]])
            self.assertEqual(result,0,output.getvalue())
            self.assertEqual(path.read_bytes(),expected)

    def test_previous_settings_migrate_only_after_complete_validation(self):
        for language in ('ru','en'):
            with self.subTest(language=language), temporary_directory() as root:
                raw=executable(language)
                service=Service()
                changes=tuple(c for p in SETTINGS if p!='game-settings'
                              for c in service.implementations[('dungeons-of-kremlin',p)].analyze({'main':raw}).changes)
                baseline=build_result({'main':raw},changes)['main']
                previous=json.loads(settings.files('wasdify').joinpath(
                    'data/kremlin_settings_legacy.json').read_text('ascii'))
                old=settings._install(baseline,previous)
                self.assertEqual(settings.restore_base(old),baseline)
                path=root/'game.exe';path.write_bytes(old)
                plan=service.plan({'main':path},SAVE)
                self.assertTrue(plan.ready,plan.issues)
                service.apply(plan)
                inner=saves.restore_base(path.read_bytes())
                self.assertEqual(inner,settings._install(settings.restore_base(inner), save_enabled=True))
                bad=bytearray(old);bad[segments(old)[-2].start+42]^=1
                path.write_bytes(bad)
                self.assertFalse(service.plan({'main':path},SAVE).ready)

    def test_both_languages_orders_reapply_and_preserved_layers(self):
        for language in ('ru','en'):
            for stages in ((SAVE+SETTINGS,), (SAVE,SETTINGS), (SETTINGS,SAVE)):
                with self.subTest(language=language,stages=stages), temporary_directory() as root:
                    path = root/'renamed.exe'
                    raw = executable(language)
                    path.write_bytes(raw)
                    service = Service()
                    for selection in stages:
                        plan = service.plan({'main':path},selection)
                        self.assertTrue(plan.ready,plan.issues)
                        service.apply(plan)
                    final = path.read_bytes()
                    self.assertTrue(saves.installed(final))
                    inner = saves.restore_base(final)
                    self.assertTrue(settings.installed(inner))
                    base = settings.restore_base(inner)
                    self.assertEqual(service.detect(path).language,language)
                    self.assertGreater(len(final),len(raw))
                    repeat = service.plan({'main':path},SAVE+SETTINGS)
                    self.assertTrue(repeat.ready,repeat.issues)
                    self.assertFalse(repeat.changes)
                    self.assertEqual(service.apply(repeat).status,'no_changes')
                    self.assertTrue(any(p.read_bytes()==raw for p in root.glob('*.bak')))
                    self.assertNotIn(b'KRSIO001',base)

    def test_explicit_dependency_and_whole_selection_before_write(self):
        with temporary_directory() as root:
            path = root/'PKR.EXE'; raw=executable(); path.write_bytes(raw)
            service=Service()
            plan=service.plan({'main':path},('save-load',))
            self.assertEqual(plan.issues[0].code,'missing_dependency')
            self.assertEqual(path.read_bytes(),raw)
            data=bytearray(raw)
            clock=next(s for s in segments(raw) if s.index==39)
            data[clock.start+0x239]^=1; path.write_bytes(data)
            plan=service.plan({'main':path},SAVE+SETTINGS)
            self.assertFalse(plan.ready)
            self.assertEqual(path.read_bytes(),bytes(data))
            self.assertFalse((root/'SETUP.EXE').exists())

    def test_tampering_rejected_even_for_another_patch(self):
        with temporary_directory() as root:
            path=root/'x.exe';path.write_bytes(executable())
            service=Service();service.apply(service.plan({'main':path},SAVE+SETTINGS))
            good=path.read_bytes();ss=segments(good)
            for at in (ss[-1].start+15,ss[-2].start+152,ss[-3].start+35,0x80+28,len(good)-1):
                data=bytearray(good);data[at]^=1;path.write_bytes(data)
                with self.subTest(at=at):
                    self.assertRaises(WasdifyError,saves.restore_base,bytes(data))
                    plan=service.plan({'main':path},('resolution-640x400',),game_id='dungeons-of-kremlin')
                    self.assertFalse(plan.ready)

    def test_header_padding_relocations_and_signature_ambiguity(self):
        raw=executable();ss=segments(raw);clock=next(s for s in ss if s.index==39)
        for kind in ('padding','relocation','chain','duplicate','missing_segment'):
            data=bytearray(raw)
            if kind=='padding':
                data[0x80+64+51*8+37]=1
            elif kind=='missing_segment':
                struct.pack_into('<H',data,0x80+64+38*8,0)
            elif kind in ('relocation','chain'):
                offset=0x239 if kind=='relocation' else 0x400
                table=settings._records(((3,4 if kind=='relocation' else 0,offset,1,0),))
                data[clock.end:clock.end+len(table)]=table
                if kind=='chain':
                    struct.pack_into('<H',data,clock.start+offset,0x239)
            else:
                # A second active copy of WASD's native anchor is ambiguous.
                profile=next(p for p in saves.profiles() if p['language']=='ru')
                player=next(s for s in ss if s.index==14)
                anchor=profile['anchors']['player']
                size=len(anchor['signature'].split())
                source=player.start+anchor['offset']
                data[clock.start+0x1000:clock.start+0x1000+size]=data[source:source+size]
            with self.subTest(kind=kind):
                self.assertRaises(WasdifyError,saves.SaveLoad().analyze,{'main':bytes(data)})

    def test_config_keys_have_no_native_command_bits(self):
        from wasdify.patches.kremlin_settings_spec import ACTIONS, initial_data
        self.assertEqual(ACTIONS[-2:],(('save',0x3f,0,True,0),('load',0x43,0,True,0)))
        self.assertEqual(initial_data()[32+19*2:32+21*2],bytes((0x3f,0,0x43,0)))
