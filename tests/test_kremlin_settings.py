"""Synthetic sparse NE containers and WASDify's own DOS artifacts only."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import struct
import unittest

from wasdify.cli import main
from wasdify.engine import build_result
from wasdify.i18n import messages
from wasdify.models import WasdifyError
from wasdify.patches import kremlin_settings as settings
from wasdify.patches.kremlin_wasd import WasdControls, profiles, segments
from wasdify.patches.kremlin_settings_spec import sensitivity
from wasdify.service import Service
from tests.support import temporary_directory
from tests.test_kremlin_wasd import executable as input_fixture

SELECTION = ("wasd", "resolution-640x400", "game-settings")


def executable(lang="ru", base=0x1000):
    raw = input_fixture(lang)
    original_segments = segments(raw)
    profile = next(p for p in profiles() if p["language"] == lang)
    context = json.loads(settings.files("wasdify").joinpath(
        "data/kremlin_settings_profiles.json").read_text("ascii"))[lang]
    # Names in profile order are explicit; never depend on a game executable.
    real = {"input":25,"mouse":21,"player":14,"reader":15} if lang=="ru" else {
        "input":11,"mouse":8,"player":5,"reader":6}
    indexes = real
    remap = {i+1:indexes[name] for i,name in enumerate(profile["anchors"])}
    count = context["segments"]
    data = bytearray(base+0x6000*7)
    ne = 0x80
    data[:2]=b"MZ"
    struct.pack_into("<I",data,60,ne)
    data[ne:ne+2]=b"NE"
    struct.pack_into("<H",data,ne+28,count)
    struct.pack_into("<H",data,ne+14,count)
    struct.pack_into("<H",data,ne+26,count)
    struct.pack_into("<H",data,ne+22,1)
    struct.pack_into("<H",data,ne+34,64)
    struct.pack_into("<H",data,ne+50,9)
    for k,v in {4:64+count*8+24,36:64+count*8,38:64+count*8,
                40:64+count*8+7,42:64+count*8+11,32:11}.items():
        struct.pack_into("<H",data,ne+k,v)
    struct.pack_into("<I",data,ne+44,ne+64+count*8+26)
    tail=bytes.fromhex(context['header_tail'])
    data[ne+64+count*8:ne+64+count*8+len(tail)]=tail
    starts={}
    for slot,s in enumerate(original_segments):
        index=remap[s.index]
        start=base+slot*0x6000
        starts[index]=start
        body=raw[s.start:s.end]
        if index==indexes['reader']:
            body=body.ljust(0x3000,b'\0')
        data[start:start+len(body)]=body
        records=[(k,f,o,remap.get(t,t),a) for k,f,o,t,a in s.records]
        # Native joystick constructor is in the input tail skipped by WASD.
        if index==indexes['input']:
            records.append((3,0,profile['anchors']['input']['offset']+0x2df,*context['joystick_init']))
        struct.pack_into('<4H',data,ne+64+(index-1)*8,start>>9,len(body),s.flags,len(body))
        table=settings._records(records)
        data[start+len(body):start+len(body)+len(table)]=table
    for slot,index in enumerate((1,2),4):
        start=base+slot*0x6000
        starts[index]=start
        struct.pack_into('<4H',data,ne+64+(index-1)*8,start>>9,0x5200,0x1d50,0x5200)
        struct.pack_into('<H',data,start+0x5200,0)
    if lang=='ru':
        start=base+6*0x6000
        starts[17]=start
        struct.pack_into('<4H',data,ne+64+16*8,start>>9,0x1000,0x1d50,0x1000)
        struct.pack_into('<H',data,start+0x1000,0)
    for c in context['contexts']:
        content=bytes.fromhex(c['expected'])
        start=starts[c['segment']]+c['offset']
        data[start:start+len(content)]=content
    # The published tail is WASD's JLE form; input_fixture is still unpatched.
    # Restore every original WASD region after the independent contexts.
    for region in profile['regions']:
        start=starts[indexes[region['anchor']]]+profile['anchors'][region['anchor']]['offset']+region['relative']
        content=bytes.fromhex(region['expected'])
        data[start:start+len(content)]=content
    at=starts[indexes['input']]+profile['anchors']['input']['offset']+0x2df
    data[at:at+4]=b'\xff\xff\0\0'
    pattern=bytes.fromhex(context['main_pattern'])
    data[starts[2]+0x100:starts[2]+0x100+len(pattern)]=pattern
    return bytes(data)


class SettingsTests(unittest.TestCase):
    def test_synthetic_install_repeat_and_every_patch_owner(self):
        for lang in ('ru','en'):
            for base in (0x1000,0x2000):
                with self.subTest(lang=lang,base=base), temporary_directory() as directory:
                    path=Path(directory)/'unknown.exe'
                    raw=executable(lang,base);path.write_bytes(raw)
                    service=Service()
                    plan=service.plan({'main':path},SELECTION)
                    self.assertTrue(plan.ready,plan.issues)
                    output=build_result({'main':raw},plan.changes)['main']
                    self.assertGreater(len(output),len(raw))
                    restored=settings.restore_base(output)
                    self.assertTrue(WasdControls().analyze({'main':restored}).already_applied)
                    path.write_bytes(output)
                    repeat=service.plan({'main':path},SELECTION)
                    self.assertTrue(repeat.ready,repeat.issues)
                    self.assertFalse(repeat.changes)
                    self.assertEqual(set(repeat.already_applied),set(SELECTION))

    def test_required_explicit_dependencies_and_backups(self):
        with temporary_directory() as directory:
            path=Path(directory)/'game.exe';raw=executable();path.write_bytes(raw)
            service=Service()
            plan=service.plan({'main':path},('game-settings',))
            self.assertEqual(plan.issues[0].code,'missing_dependency')
            self.assertEqual(path.read_bytes(),raw)
            plan=service.plan({'main':path},SELECTION)
            result=service.apply(plan)
            self.assertEqual(result.backup.read_bytes(),raw)
            repeat=service.apply(service.plan({'main':path},SELECTION))
            self.assertEqual(repeat.status,'no_changes')

    def test_corrupted_headers_hooks_payloads_and_partial_states(self):
        raw=executable()
        baseline=build_result({'main':raw},WasdControls().analyze({'main':raw}).changes)['main']
        output=settings._install(baseline)
        ss=segments(output)
        for at in (0x80+28,0x80+44,ss[-1].start+18,ss[-2].start+50,
                   next(s for s in ss if s.index==25).start+0x1639+0x18,len(output)-1):
            damaged=bytearray(output);damaged[at]^=1
            with self.subTest(at=at), self.assertRaises(WasdifyError):
                settings.GameSettings().analyze({'main':bytes(damaged)})
        self.assertRaises(WasdifyError,settings.GameSettings().analyze,{'main':raw})
        damaged=bytearray(baseline);damaged[0x80+64+51*8]^=1
        self.assertRaises(WasdifyError,settings.GameSettings().analyze,{'main':bytes(damaged)})

    def test_ne_safety_missing_ambiguous_and_occupied_header_padding(self):
        raw=executable();w=build_result({'main':raw},WasdControls().analyze({'main':raw}).changes)['main']
        for kind in ('padding','main','duplicate','relocation'):
            b=bytearray(w);ss=segments(w);main=next(s for s in ss if s.index==2)
            if kind=='padding':b[0x80+64+51*8+37]=1
            elif kind=='main':b[main.start+0x100]^=1
            elif kind=='duplicate':b[main.start+0x200:main.start+0x214]=b[main.start+0x100:main.start+0x114]
            else:
                table=settings._records(((3,0,0x110,1,0),))
                b[main.end:main.end+len(table)]=table
            with self.subTest(kind=kind),self.assertRaises(WasdifyError):settings._install(bytes(b))

    def test_dos_language_builds_are_distinct_and_service_is_explicit(self):
        with temporary_directory() as directory:
            service=Service()
            ru=service.export_setup(directory,'ru');raw=ru.path.read_bytes()
            self.assertEqual(raw[:2],b'MZ')
            self.assertIn(messages('ru')['dos_title'].replace('ё','е').replace('Ё','Е').encode('cp866'),raw)
            self.assertEqual(service.export_setup(directory,'ru').status,'no_changes')
            with self.assertRaises(WasdifyError):service.export_setup(directory,'en')
            en=service.export_setup(directory,'en',overwrite=True)
            self.assertEqual(en.backup.read_bytes(),raw)
            self.assertIn(b'Dungeons of Kremlin setup',en.path.read_bytes())
            with self.assertRaises(WasdifyError):service.export_setup(directory,'fr')
            self.assertFalse((Path(directory)/'KREMLIN.INI').exists())

    def test_setup_cli_language_independent_of_interface(self):
        with temporary_directory() as directory,redirect_stdout(StringIO()) as out:
            result=main(['setup',str(directory),'--lang','ru','--setup-lang','en','--json'])
            self.assertEqual(result,0)
            self.assertEqual(json.loads(out.getvalue())['setup']['language'],'en')
            self.assertIn(b'Dungeons of Kremlin setup',(Path(directory)/'SETUP.EXE').read_bytes())

    def test_sensitivity_and_catalog_parity(self):
        self.assertEqual(float(sensitivity(7)),0.2)
        self.assertAlmostEqual(float(sensitivity(5)),0.15)
        self.assertEqual(set(messages('ru')),set(messages('en')))
        self.assertRaises(ValueError,sensitivity,10)
