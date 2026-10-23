"""Step 6: exercise shipped save bytes installed by the final Windows CLI.

Only disposable mqa copies are changed. DOSBox supplies input/observations and
controlled health/disk faults; ordinary guest code owns capture and restoration.
No research gates, injected code, or host-side world restoration are used.
"""
import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import shutil
import struct
import subprocess
import time

from profile_kremlin import ROOT
from verify_kremlin_save_failures import failed_request, invalid_health
from verify_kremlin_save_native import process, records
from verify_kremlin_save_patch import GAME, SAVE
from verify_kremlin_settings_dosbox import wait_for
from wasdify.engine import build_result
from wasdify.service import Service


def select_level(d):
    if d.level == 1:
        return
    deadline = time.monotonic() + 40
    while d.view().u16(d.ds + d.shift + 0x1fae) != 1:
        if time.monotonic() > deadline:
            raise TimeoutError('level_menu')
        d.key(0x39)
        time.sleep(.3)
    time.sleep(.5)
    for _ in range(d.level - 1):
        d.key(0x50)
        time.sleep(.25)


def request(d, scan):
    sequence = d.check().u16(d.transport + 400)
    d.key(scan)
    wait_for(lambda: d.check().u16(d.transport + 400) != sequence, timeout=40)
    d.wait_idle()


def die(d, action):
    # Ordinary payload has no host pause gate. Arrange death, then let the game
    # run its own death path, dialog, restart/restore and attempt bookkeeping.
    sequence = d.check().u16(d.transport + 400)
    d.api.put(str(d.hero() + 0x5e), bytes(2))
    wait_for(lambda: d.view().u16(d.transport + 44) == 2
             and d.view().u16(d.transport + 254) == 1, timeout=40)
    d.choose(action)
    wait_for(lambda: d.check().u16(d.transport + 400) != sequence, timeout=40)
    d.wait_idle()
    if action == 'restart':
        for _ in range(3):
            d.key(0x1c)
            time.sleep(.3)


def trial(installer, language, level, full):
    work = ROOT / 'mqa' / ('save-release-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    folder, filename = ('RUS', 'PKR.EXE') if language == 'ru' else ('ENG', 'PKE.EXE')
    source = ROOT / 'originals/games/pkr' / folder
    game = work / folder
    shutil.copytree(source, game)
    service = Service()
    chosen = tuple(p.id for p in service.list_patches(GAME)) if full else SAVE
    raw_exe = (game / filename).read_bytes()
    plan = service.plan({'main': game / filename}, chosen)
    assert plan.ready and plan.detection.method == 'hash', plan.issues
    expected = build_result({'main': raw_exe}, plan.changes)['main']
    arguments = [arg for patch in chosen for arg in ('--patch', patch)]
    report = dict(language=language, level=level, patches=chosen, status='running',
                  installer_sha256=sha256(installer.read_bytes()).hexdigest(),
                  original_sha256=sha256(raw_exe).hexdigest())
    try:
        for status in ('applied', 'no_changes'):
            completed = subprocess.run([str(installer), '--json', 'apply', str(game / filename),
                                        *arguments], cwd=work, capture_output=True,
                                       text=True, check=True, timeout=60)
            assert json.loads(completed.stdout)['result']['status'] == status
        assert (game / filename).read_bytes() == expected
        assert any(p.read_bytes() == raw_exe for p in game.glob(filename + '*.bak*'))
        report['patched_sha256'] = sha256(expected).hexdigest()
        save_scan, load_scan = (0x40, 0x44) if full else (0x3f, 0x43)
        if full:
            (game / 'KREMLIN.INI').write_text(
                '[wasdify]\nversion=1\n[video]\nmode=1\n[keys]\n'
                'save=64,0\nload=68,0\nfire=63,0\n', encoding='ascii')
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
{filename} {'' if full else '-r0'}
exit
''', encoding='ascii')
        with process(work, 'first', report, level, language) as d:
            select_level(d)
            d.enter()
            assert d.check().u16(d.transport + 426) == ord(str(level))
            # Exercise real gameplay before saving, rather than only the entry.
            d.key(0x3f if full else 0x1d)
            time.sleep(.4)
            request(d, save_scan)
            raw = d.save_path().read_bytes()
            saved = records(raw)
            assert len(saved) > 1 and any(r['events'] for r in saved.values())
            report['saved_objects'] = len(saved)
            report['saved_events'] = sum(r['events'] for r in saved.values())
            health = next(r['fields']['health'] for r in saved.values() if r['type'] == 1)
            attempt = d.check().u16(d.ds + d.shift + 0x1fae)
            for _ in range(3):
                d.api.put(str(d.hero() + 0x5e), struct.pack('<H', max(1, health - 7)))
                request(d, load_scan)
                assert d.check().u16(d.transport + 402) == 2
                assert d.check().u16(d.hero() + 0x5e) == health
            report['same_process_loads_and_guest_logical_recapture'] = 3
            # Native restore recaptures every logical record and checks CRC and
            # length before reporting success. Also prove simulation continues:
            # the installed movement path changes restored player coordinates.
            before = d.check().read(d.hero() + 0x26, 12)
            scan = 0x11 if full else 0x48
            key = d.ds + d.shift + 0x2155 + scan
            d.api.put(str(key), b'\x03')
            try:
                wait_for(lambda: d.check().read(d.hero() + 0x26, 12) != before)
            finally:
                d.api.put(str(key), bytes(1))
            request(d, load_scan)
            report['movement_after_restore'] = True
            die(d, 'load')
            assert d.check().u16(d.hero() + 0x5e) == health
            assert d.check().u16(d.ds + d.shift + 0x1fae) == attempt
            die(d, 'restart')
            assert d.check().u16(d.ds + d.shift + 0x1fae) == attempt + 1
            assert d.save_path().read_bytes() == raw
            report['death_load_restart_attempt_and_preserved_save'] = True
        with process(work, 'second', report, level, language) as d:
            select_level(d)
            d.enter(choice='load')
            assert d.check().u16(d.hero() + 0x5e) == health
            assert d.check().u16(d.ds + d.shift + 0x1fae) == attempt
            request(d, load_scan)
            request(d, save_scan)
            latest = d.save_path().read_bytes()
            assert d.save_path('PRV').read_bytes() == raw
            report['new_process_repeated_load_and_rotation'] = True
            for broken in (latest[:-1], invalid_health(latest)):
                d.save_path().write_bytes(broken)
                request(d, load_scan)
                assert d.check().u16(d.transport + 434) == 1
                assert d.check().u16(d.hero() + 0x5e) == health
            report['truncated_and_semantically_invalid_sav_fall_back'] = True
            d.save_path('TMP').mkdir()
            failed_request(d, save_scan)
            assert d.save_path('PRV').read_bytes() == raw
            d.save_path('TMP').rmdir()
            request(d, load_scan)
            report['dos_write_refusal_preserves_loadable_record'] = True
            d.save_path('PRV').write_bytes(b'broken')
            hero = d.hero()
            failed_request(d, load_scan)
            assert d.hero() == hero
            assert d.check().u16(hero + 0x5e) == health
            report['all_invalid_rejected_before_world_mutation'] = True
            d.save_path().unlink()
            d.save_path('PRV').write_bytes(raw)
            d.save_path('TMP').write_bytes(latest)
            request(d, load_scan)
            assert d.check().u16(d.transport + 434) == 1
            d.save_path('PRV').unlink()
            request(d, load_scan)
            assert d.check().u16(d.transport + 434) == 2
            assert d.save_path('TMP').read_bytes() == latest
            report['interrupted_rotation_and_first_save_recovered'] = True
        with process(work, 'third', report, level, language) as d:
            select_level(d)
            d.enter(choice='load')
            assert d.check().u16(d.transport + 434) == 2
            assert d.check().u16(d.hero() + 0x5e) == health
            assert d.save_path('TMP').read_bytes() == latest
            report['orphan_tmp_restored_in_new_process'] = True
        assert (source / filename).read_bytes() == raw_exe
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        (work / 'report.json').write_text(json.dumps(report, indent=2), encoding='ascii')
        print(work, flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path, default=ROOT / 'dist/WASDify.exe')
    parser.add_argument('--language', choices=('ru', 'en'), default='ru')
    parser.add_argument('--level', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--minimal', action='store_true')
    parser.add_argument('--matrix', action='store_true')
    args = parser.parse_args()
    targets = ((lang, level, full) for full in (False, True) for lang in ('ru', 'en')
               for level in (1, 2, 3)) if args.matrix else ((args.language, args.level, not args.minimal),)
    for language, level, full in targets:
        print(json.dumps(trial(args.installer.resolve(), language, level, full)), flush=True)
