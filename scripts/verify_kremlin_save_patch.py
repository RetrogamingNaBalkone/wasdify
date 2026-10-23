"""Step 5 integration evidence on disposable copies, never unit-test fixtures.

Service installs packaged bytes. The DOSBox API supplies input and observations;
the guest performs save/load. Originals are read only. Reports stay in mqa/.
"""
import argparse
from datetime import datetime
import itertools
from hashlib import sha256
import json
from pathlib import Path
import shutil
import struct
import time

from profile_kremlin import ROOT
from verify_kremlin_save_native import process
from verify_kremlin_settings_dosbox import wait_for
from wasdify.engine import build_result
from wasdify.patches import kremlin_save_native as saves
from wasdify.service import Service

GAME = 'dungeons-of-kremlin'
SAVE = ('weapon-selection-fix','save-load')
SETTINGS = ('wasd','resolution-640x400','game-settings')


def static_matrix():
    service = Service()
    checks = 0
    for language,folder,filename in (('ru','RUS','PKR.EXE'),('en','ENG','PKE.EXE')):
        raw = (ROOT/'originals/games/pkr'/folder/filename).read_bytes()
        assert any(v.language==language and v.sha256==sha256(raw).hexdigest()
                   for v in service.catalog.game(GAME).versions)
        optional = ('wasd','resolution-640x400','render-optimization','numeric-hud','font-scaling')
        for flags in itertools.product((False,True),repeat=len(optional)):
            selected = SAVE+tuple(p for p,enabled in zip(optional,flags) if enabled)
            selections = [selected]
            if all(p in selected for p in SETTINGS[:2]):
                selections.append(selected+('game-settings',))
            for chosen in selections:
                analysis = saves.analyze_selection(raw,chosen,service.implementations,GAME)
                output = build_result({'main':raw},tuple(c for a in analysis.values() for c in a.changes))['main']
                assert all(not a.issues for a in analysis.values())
                repeat = saves.analyze_selection(output,chosen,service.implementations,GAME)
                assert all(a.already_applied and not a.changes for a in repeat.values())
                for existing in (SAVE,tuple(p for p in chosen if p not in SAVE)):
                    if not existing:
                        continue
                    # Exercise the same planner before and after adding saves.
                    if 'game-settings' in existing:
                        from wasdify.patches.kremlin_settings import analyze_selection
                        first = analyze_selection(raw,existing,service.implementations,GAME)
                    else:
                        first = saves.analyze_selection(raw,existing,service.implementations,GAME)
                    partial = build_result({'main':raw},tuple(c for a in first.values() for c in a.changes))['main']
                    missing = tuple(p for p in chosen if p not in existing)
                    if not missing:
                        assert partial == output
                        continue
                    # Explicit prerequisites remain selected for Service-style calls.
                    remaining = tuple(dict.fromkeys(missing+SAVE)) if 'save-load' in missing else missing
                    second = saves.analyze_selection(partial,remaining,service.implementations,GAME)
                    final = build_result({'main':partial},tuple(c for a in second.values() for c in a.changes))['main']
                    assert final == output,(language,chosen,existing)
                checks += 1
    return checks


def trial(language='ru',level=1,full=True,remap=False,mode=1):
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    work = ROOT/'mqa'/('save-patch-'+stamp)
    folder,filename = ('RUS','PKR.EXE') if language=='ru' else ('ENG','PKE.EXE')
    game = work/folder
    shutil.copytree(ROOT/'originals/games/pkr'/folder,game)
    service = Service()
    chosen = tuple(p.id for p in service.list_patches(GAME)) if full else SAVE
    plan = service.plan({'main':game/filename},chosen)
    assert plan.detection.method=='hash'
    assert plan.ready,plan.issues
    service.apply(plan)
    assert service.apply(service.plan({'main':game/filename},chosen)).status=='no_changes'
    save_scan,load_scan = (0x40,0x44) if remap else (0x3f,0x43)
    if full:
        (game/'KREMLIN.INI').write_text(f'[wasdify]\nversion=1\n[video]\nmode={mode}\n'
            '[mouse]\nsensitivity=5\n[keys]\n'+
            (f'save={save_scan},0\nload={load_scan},0\nfire=63,0\n' if remap else ''),encoding='ascii')
    (work/'dosbox.conf').write_text(f'''[sdl]
fullscreen = off
[dosbox]
machine = svga_s3
memsize = 32
[cpu]
core = normal
cputype = pentium
cpu_cycles = 60000
cpu_cycles_protected = 60000
[webserver]
webserver_enabled = off
[autoexec]
mount -u c
mount c "{game}"
c:
{filename} {'' if full else '-r0'}
exit
''',encoding='ascii')
    report = dict(language=language,level=level,patches=chosen,remap=remap,mode=mode if full else 0,status='running')

    def select_level(d):
        # Select the actual menu entry; no executable or resource-name rewrites.
        if level != 1:
            deadline=time.monotonic()+40
            while d.view().u16(d.ds+d.shift+0x1fae)!=1:
                if time.monotonic()>deadline:
                    raise TimeoutError('level_menu')
                d.key(0x39)  # dismiss splash without confirming the level menu
                time.sleep(.3)
            time.sleep(.5)
            for _ in range(level-1):
                d.key(0x50)
                time.sleep(.25)

    def request(d,scan):
        sequence=d.check().u16(d.transport+400)
        d.key(scan)
        wait_for(lambda:d.check().u16(d.transport+400)!=sequence,timeout=40)
        d.wait_idle()

    try:
        with process(work,'first',report,level,language) as d:
            select_level(d)
            d.enter()
            assert d.check().u16(d.transport+426)==ord(str(level))
            request(d,save_scan)
            raw=d.save_path().read_bytes()
            health=d.check().u16(d.hero()+0x5e)
            assert raw[:4]==b'KRN1'
            if remap:
                sequence=d.check().u16(d.transport+400)
                d.key(0x3f)  # now fire, never save
                time.sleep(.4)
                assert d.check().u16(d.transport+400)==sequence
                assert d.save_path().read_bytes()==raw
            for _ in range(3):
                d.api.put(str(d.hero()+0x5e),struct.pack('<H',max(1,health-7)))
                request(d,load_scan)
                assert d.check().u16(d.hero()+0x5e)==health
            if full:
                # Exercise the restored hero through the installed input path.
                memory=d.api.get('memory/0/0x2000000')
                marker=b'WASDify-CFG-v1\0\0'
                assert memory.count(marker)==1
                cfg=memory.index(marker)
                for scan,action in ((0x11,0),(0x20,3)):
                    key=d.ds+d.shift+0x2155+scan
                    before=d.check().read(d.hero()+0x26,12)
                    d.api.put(str(key),b'\x03')
                    try:
                        wait_for(lambda:d.check().read(cfg+80+action,1)==b'\x01')
                        wait_for(lambda:d.check().read(d.hero()+0x26,12)!=before)
                    finally:
                        d.api.put(str(key),b'\x00')
                request(d,load_scan)
                report['wasd_commands_after_restore']=True
            report['same_process_loads']=3
        with process(work,'second',report,level,language) as d:
            select_level(d)
            d.enter(choice='load')
            assert d.check().u16(d.hero()+0x5e)==health
            request(d,save_scan)
            assert d.save_path('PRV').read_bytes()==raw
            report['new_process_and_previous_record']=True
        report['status']='passed'
    except Exception as error:
        report['status']='failed'
        report['error']=repr(error)
        raise
    finally:
        (work/'report.json').write_text(json.dumps(report,indent=2),encoding='ascii')
        print(work,flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static',action='store_true')
    parser.add_argument('--language',choices=('ru','en'),default='ru')
    parser.add_argument('--level',type=int,choices=(1,2,3),default=1)
    parser.add_argument('--minimal',action='store_true')
    parser.add_argument('--remap',action='store_true')
    parser.add_argument('--mode',type=int,choices=range(5),default=1)
    args=parser.parse_args()
    if args.static:
        print(json.dumps({'static_combinations':static_matrix(),'status':'passed'}))
    else:
        print(json.dumps(trial(args.language,args.level,not args.minimal,args.remap,args.mode),indent=2))
