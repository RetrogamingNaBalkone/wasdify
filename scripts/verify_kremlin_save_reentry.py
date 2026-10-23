"""Exercise level exits and reentry in disposable Service-installed games.

The guest owns menus and save/load. Host writes are limited to keyboard input
and the hero's native exit sentinel used to reach level completion quickly.
Originals and existing working games are never modified.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import time

from profile_kremlin import ROOT
from verify_kremlin_save_native import process
from verify_kremlin_settings_dosbox import wait_for
from wasdify.service import Service
from wasdify.engine import build_result


def run(language='ru', full=True, installer=None, completion_load=False):
    work = ROOT / 'mqa' / ('save-reentry-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    folder, filename = ('RUS', 'PKR.EXE') if language == 'ru' else ('ENG', 'PKE.EXE')
    game = work / folder
    shutil.copytree(ROOT / 'originals/games/pkr' / folder, game)
    service = Service()
    chosen = tuple(p.id for p in service.list_patches('dungeons-of-kremlin')) if full else ('weapon-selection-fix', 'save-load')
    plan = service.plan({'main': game / filename}, chosen)
    assert plan.ready, plan.issues
    if installer is None:
        service.apply(plan)
    else:
        expected = build_result({'main': (game / filename).read_bytes()}, plan.changes)['main']
        args = [arg for patch in chosen for arg in ('--patch', patch)]
        for status in ('applied', 'no_changes'):
            completed = subprocess.run([str(installer), '--json', 'apply', str(game / filename), *args],
                                       cwd=work, capture_output=True, text=True, check=True, timeout=60)
            assert json.loads(completed.stdout)['result']['status'] == status
        assert (game / filename).read_bytes() == expected
    (work / 'dosbox.conf').write_text(f'''[sdl]
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
{filename} -r0
''', encoding='ascii')
    report = dict(language=language, patches=chosen, installer=str(installer) if installer else None,
                  status='running', transitions=[])
    def finish_intro(d):
        def ready():
            if d.check().u16(d.ds + d.shift + 0x1fae) >= 2:
                return True
            d.key(0x1c)
            time.sleep(.3)
        wait_for(ready, timeout=40)
    try:
        with process(work, 'reentry', report, language=language) as d:
            d.enter()
            finish_intro(d)
            print('entered first level', flush=True)
            for reason, saved, choice in (('escape', False, None), ('escape', True, 'restart'),
                                          ('escape', True, 'load'),
                                          ('complete', True, 'load' if completion_load else 'restart')):
                if saved:
                    d.save()
                print('leaving via ' + reason, flush=True)
                if reason == 'escape':
                    d.key(0x01)
                else:
                    # Native exit event 1000 sets health=-1; the ordinary
                    # health check then returns completion status 3.
                    d.api.put(str(d.hero() + 0x5e), b'\xff\xff')
                # Native level selection resets the attempt before waiting.
                def level_menu():
                    if d.check().u16(d.ds + d.shift + 0x1fae) == 1:
                        return True
                    if reason == 'complete':
                        d.key(0x1c)  # dismiss the native completion screen
                        time.sleep(.3)
                wait_for(level_menu, timeout=40)
                time.sleep(.5)
                print('level menu', flush=True)
                # Old bindings persist until the early hook; observe that hook
                # before using Driver.enter's fresh-process binding condition.
                d.key(0x1c)
                def early_gate():
                    m = d.check()
                    if m.u16(d.transport + 44) == 3 or m.u16(d.transport + 198) == 0:
                        return True
                    # ENG also shows the native story before the splash hook.
                    d.key(0x1c)
                    time.sleep(.3)
                wait_for(early_gate, timeout=30)
                if choice is not None:
                    wait_for(lambda: d.check().u16(d.transport + 44) == 3
                             and d.check().u16(d.transport + 254) == 1)
                    sequence = d.check().u16(d.transport + 400)
                    d.choose(choice)
                    wait_for(lambda: d.check().u16(d.transport + 44) == 0)
                    if choice == 'load':
                        wait_for(lambda: d.check().u16(d.transport + 400) != sequence)
                        d.wait_idle()
                d.enter()
                finish_intro(d)
                assert d.hero() is not None
                report['transitions'].append(dict(exit=reason, saved=saved, choice=choice))
                print(report['transitions'][-1], flush=True)
        report['status'] = 'passed'
    except Exception as error:
        report['status'], report['error'] = 'failed', repr(error)
        raise
    finally:
        (work / 'report.json').write_text(json.dumps(report, indent=2), encoding='ascii')
        print(work, flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=('ru', 'en'), default='ru')
    parser.add_argument('--minimal', action='store_true')
    parser.add_argument('--installer', type=Path)
    parser.add_argument('--completion-load', action='store_true',
                        help='Also exercise the separate early-load path after completion')
    args = parser.parse_args()
    print(json.dumps(run(args.language, not args.minimal,
                         args.installer.resolve() if args.installer else None,
                         args.completion_load), indent=2))
