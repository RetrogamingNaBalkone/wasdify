"""Exercise native weapon pickup and repeated switching in isolated DOSBox copies.

Development only. Original game inputs remain read-only; all outputs stay in mqa/.
Uses the local DOSBox HTTP API and helpers from the settings verification script.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
import shutil
import struct
import subprocess
import time

from profile_kremlin import Api, ROOT
from verify_kremlin_settings_dosbox import text, wait_for
from wasdify.patches.kremlin_weapons import WeaponSelectionFix, TIMER_REPLACEMENT
from wasdify.service import Service


def trial(language, rounds, delay, mode, previous):
    work = ROOT / 'mqa' / ('weapon-switch-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    game = work / ('RUS' if language == 'ru' else 'ENG')
    work.mkdir(parents=True)
    shutil.copytree(ROOT / 'originals/games/pkr' / game.name, game)
    exe = game / ('PKR.EXE' if language == 'ru' else 'PKE.EXE')
    source = exe.read_bytes()
    service = Service()
    selected = tuple(p.id for p in service.list_patches('dungeons-of-kremlin'))
    plan = service.plan({'main': exe}, selected)
    assert plan.ready, plan.issues
    service.apply(plan)
    if previous:
        # Reproduce the previous release on this disposable copy by reverting
        # only the context-validated cursor edit. Other patches remain identical.
        timer = WeaponSelectionFix().analyze({'main': source}).changes[-1]
        data = bytearray(exe.read_bytes())
        assert data[timer.offset:timer.offset+4] == TIMER_REPLACEMENT
        data[timer.offset:timer.offset+4] = timer.expected
        exe.write_bytes(data)
    (game / 'KREMLIN.INI').write_text(
        f'[wasdify]\nversion=1\n[video]\nmode={mode}\n', encoding='ascii')
    port = 8130 if language == 'ru' else 8131
    api = Api(port)
    try:
        api.get('dosbox/info')
    except OSError:
        pass
    else:
        raise RuntimeError('Test port already in use')
    config = work / 'dosbox.conf'
    config.write_text(f'''[sdl]
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
webserver_enabled = on
webserver_bind_address = 127.0.0.1
webserver_port = {port}
[autoexec]
mount -u c
mount c "{game}"
c:
{exe.name}
''', encoding='ascii')
    environment = dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')
    report = {'language': language, 'previous': previous, 'rounds': rounds,
              'delay': delay, 'mode': mode, 'switches': [],
              'sha256': hashlib.sha256(exe.read_bytes()).hexdigest()}
    with (work / 'stdout.log').open('wb') as out, (work / 'stderr.log').open('wb') as err:
        process = subprocess.Popen(
            [str(ROOT / 'mqa/dosbox/dosbox.exe'), '-conf', str(config), '-noconsole'],
            cwd=ROOT / 'mqa/dosbox', env=environment, stdout=out, stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_for(lambda: api.get('dosbox/info'))
            anchor = b'OVERFLOW\0UNDERFLOW\0TLOSS\0PLOSS'

            def loaded():
                data = api.get('memory/0/0x2000000')
                return data if anchor in data and b'WASDify-CFG-v1\0\0' in data else None

            memory = wait_for(loaded)
            assert memory.count(anchor) == 1
            ds = memory.find(anchor) - (0x1082 if language == 'ru' else 0x1132)
            keys = ds + (0x2155 if language == 'ru' else 0x2205)

            def key(scan, hold=.12):
                pending = struct.unpack('<H', api.get(f'memory/{keys-3}/2'))[0]
                api.put(str(keys+scan), b'\x03')
                api.put(str(keys-3), struct.pack('<HB', (pending+1) & 0xffff, scan))
                time.sleep(hold)
                api.put(str(keys+scan), b'\x02')

            for _ in range(8):
                time.sleep(.7)
                key(0x1c)
            memory = api.get('memory/0/0x2000000')
            cfg = memory.find(b'WASDify-CFG-v1\0\0')

            def base(desc):
                return int.from_bytes(desc[2:4], 'little') + (desc[4] << 16) + (desc[7] << 24)

            tables = set()
            for access in (0x82, 0xa2, 0xc2, 0xe2):
                pos = 5
                while True:
                    pos = memory.find(bytes((access,)), pos)
                    if pos < 0:
                        break
                    desc = memory[pos-5:pos+3]
                    pos += 1
                    if len(desc) != 8:
                        continue
                    limit = int.from_bytes(desc[:2], 'little')
                    table = base(desc)
                    if not 0x100 <= limit < 65536 or not 0 < table < len(memory)-limit:
                        continue
                    bases = {base(memory[x:x+8]) for x in range(table, table+limit-6, 8)}
                    if ds in bases and cfg in bases:
                        tables.add(table)
            assert len(tables) == 1, tables
            table = tables.pop()

            def ptr(address):
                offset, selector = struct.unpack('<HH', api.get(f'memory/{address}/4'))
                assert selector and selector & 4
                return base(api.get(f'memory/{table+(selector & ~7)}/8')) + offset

            level = ptr(ds + (0x266a if language == 'ru' else 0x271a))
            player = ptr(level+0x442)
            geometry = struct.unpack('<HH', api.get(
                f'memory/{ds+(0xc1fc if language == "ru" else 0xc2ac)}/4'))
            report['geometry'] = geometry
            assert geometry == ((320, 200), (640, 400), (640, 480),
                                (800, 600), (1024, 768))[mode], geometry
            def state():
                return struct.unpack('<HH', api.get(f'memory/{player+0xce}/4'))

            cell = struct.unpack('<h', api.get(f'memory/{level+0x738}/2'))[0]
            stride = struct.unpack('<H', api.get(f'memory/{level+0x742}/2'))[0]
            assert cell == 480 and stride == 64
            layer = api.get(f'memory/{ds+(0x718e if language == "ru" else 0x723e)}/4096')
            # Move onto existing pickup tiles in guest RAM; the native game
            # performs pickup, object initialization and animation scheduling.
            for char, slot in ((87, 1), (86, 2)):
                index = layer.find(bytes((char,)))
                assert index >= 0
                api.put(str(player+0x26), struct.pack('<i', index % stride * cell + cell//2))
                api.put(str(player+0x2e), struct.pack('<i', index // stride * cell + cell//2))
                key(0x11, .15)
                time.sleep(1)
                assert state() == (slot, slot), state()
            observed = set()
            for n in range(rounds):
                if delay >= .4:
                    def ready():
                        current, pending = state()
                        weapon = ptr(player+0xaa+current*4)
                        flags = struct.unpack('<H', api.get(f'memory/{weapon+0x83}/2'))[0]
                        return current == pending and bool(flags & 4)
                    # A selected weapon can still be rising. The native game
                    # ignores a new switch until its animation is ready.
                    wait_for(ready, timeout=10)
                key((2, 3)[n % 2])
                time.sleep(delay)
                if delay >= .4:
                    expected = (1+n % 2, 1+n % 2)
                    wait_for(lambda: state() == expected, timeout=10)
                screen = text(api)
                assert 'EXCEPTION' not in screen, screen
                current = state()
                observed.add(current[0])
                report['switches'].append(current)
            time.sleep(1)
            assert 'EXCEPTION' not in text(api), text(api)
            if delay >= .4:
                assert observed == {1, 2}, observed
            report['status'] = 'passed'
        except Exception:
            report['status'] = 'failed'
            if 'player' in locals():
                report['player'] = api.get(f'memory/{player}/224').hex()
                report['cpu'] = json.loads(api.get('cpu/state'))
                report['weapons'] = []
                for slot in (1, 2):
                    address = ptr(player+0xaa+slot*4)
                    report['weapons'].append(api.get(f'memory/{address}/136').hex())
            (work / 'failure-screen.txt').write_text(text(api), encoding='utf-8')
            raise
        finally:
            (work / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            process.terminate()
            process.wait(timeout=10)
            print(work, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('language', choices=('ru', 'en'))
    parser.add_argument('--rounds', type=int, default=64)
    parser.add_argument('--delay', type=float, default=.4)
    parser.add_argument('--mode', type=int, choices=(0, 1, 2, 3, 4), default=0)
    parser.add_argument('--previous', action='store_true')
    args = parser.parse_args()
    if args.rounds < 2 or args.delay < 0:
        parser.error('rounds must be at least 2; delay must be nonnegative')
    trial(args.language, args.rounds, args.delay, args.mode, args.previous)
