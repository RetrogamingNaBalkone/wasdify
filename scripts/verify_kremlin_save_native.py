"""Observe autonomous save/load in disposable DOSBox processes.

The API is used only for test input, observation and controlled positioning.
The guest captures, writes, validates and restores every save itself.
"""
from contextlib import contextmanager
from datetime import datetime
import json
import os
import struct
import subprocess
import time

from prepare_kremlin_save_native import prepare, ROOT
from profile_kremlin import Api
from inspect_kremlin_save_world import Memory, locate_ldt, observe, pause_at_boundary, step_at_boundary
from verify_kremlin_save_io import MAGIC
from verify_kremlin_settings_dosbox import wait_for, text
from build_kremlin_save_native import layout_fields


def records(raw):
    """Read guest-produced scalar records for assertions, never restore a world."""
    cursor = 64 + 16384 + struct.unpack_from('<H', raw, 32)[0] * 6
    result = {}
    for _ in range(struct.unpack_from('<H', raw, 30)[0]):
        size, kind, variant, template, image, impact, events, reserved, order, key = struct.unpack_from('<HBBHHHBBHH', raw, cursor)
        fields = {}
        at = cursor + 16
        for name, (_, wire) in layout_fields(kind, variant).items():
            fields[name] = struct.unpack_from('<' + wire, raw, at)[0]
            at += struct.calcsize('<' + wire)
        result[key] = {'type': kind, 'template': template, 'fields': fields, 'events': events}
        cursor += size
    assert cursor == len(raw)
    return result


class Driver:
    def __init__(self, api, transport, ds, ldt, game):
        self.api, self.transport, self.ds, self.ldt, self.game = api, transport, ds, ldt, game

    def view(self):
        return Memory(self.api.get('memory/0/0x2000000'), self.ldt)

    def key(self, scan):
        at = self.ds + 0x2152
        count = struct.unpack('<H', self.api.get(f'memory/{at}/2'))[0]
        self.api.put(str(at + 3 + scan), b'\x03')
        self.api.put(str(at), struct.pack('<HB', (count + 1) & 65535, scan))
        time.sleep(.08)
        self.api.put(str(at + 3 + scan), b'\x02')

    def check(self):
        m = self.view()
        if m.u16(self.transport + 242):
            raise RuntimeError('native_failed: ' + m.read(self.transport + 192, 64).hex())
        return m

    def hero(self, m=None):
        m = m or self.check()
        world = m.pointer(self.ds + 0x266a, 0x74a)
        return m.pointer(world + 0x442, 0xde) if world else None

    def enter(self, *, choice=None):
        end = time.monotonic() + 60
        choice_seen = False
        while time.monotonic() < end:
            m = self.check()
            if m.u16(self.transport + 44) == 3 and m.u16(self.transport + 194):
                if choice is not None and not choice_seen and m.u16(self.transport + 254):
                    assert m.pointer(self.ds + 0x266a) is None
                    assert m.u16(self.ds + 0x1fae) == 1
                    if choice == 'restart':
                        self.key(0x48)
                    self.key(0x1c)
                    choice_seen = True
            elif m.u32(self.transport + 196):
                if choice == 'load':
                    wait_for(lambda: self.check().u16(self.transport + 400) != 0)
                    self.wait_idle()
                else:
                    for _ in range(4):
                        self.key(0x1c)
                        time.sleep(.4)
                assert choice_seen == (choice is not None)
                return choice_seen
            else:
                self.key(0x1c)
            time.sleep(.25)
        raise TimeoutError('native_level_entry')

    def wait_idle(self):
        def idle():
            m = self.check()
            return (m.u16(self.transport + 200) == 0 and m.u16(self.transport + 202) == 0
                and m.u16(self.transport + 228) == 0 and m.u16(self.transport + 44) == 0)
        wait_for(idle, timeout=30)

    def save(self):
        sequence = self.check().u16(self.transport + 400)
        self.key(0x3f)
        wait_for(lambda: self.check().u16(self.transport + 400) != sequence)
        self.wait_idle()
        return (self.game / 'KRN1.SAV').read_bytes()

    def load(self, health, *, check_health=True):
        m = self.check()
        sequence = m.u16(self.transport + 400)
        hero = self.hero(m)
        self.api.put(str(hero + 0x5e), struct.pack('<H', max(1, health - 7)))
        self.key(0x43)
        wait_for(lambda: self.check().u16(self.transport + 400) != sequence)
        self.wait_idle()
        m = self.check()
        assert m.u16(self.transport + 402) == 2
        if check_health:
            assert m.u16(self.hero(m) + 0x5e) == health

    def release(self):
        self.api.put(str(self.transport + 46), b'\x01\0')
        wait_for(lambda: self.api.get(f'memory/{self.transport + 44}/2') == bytes(2))

    def death(self, action, *, pending_save=False):
        pause_at_boundary(self.api, self.transport)
        if pending_save:
            self.api.put(str(self.ds + 0x2194), b'\x02')
            step_at_boundary(self.api, self.transport)
            assert self.check().u16(self.transport + 200) == 3
        m = self.check()
        sequence = m.u16(self.transport + 400)
        self.api.put(str(self.hero(m) + 0x5e), bytes(2))
        self.release()
        wait_for(lambda: self.api.get(f'memory/{self.transport + 44}/2') == b'\x02\0')
        wait_for(lambda: self.view().u16(self.transport + 254) == 1)
        if action == 'restart':
            self.key(0x48)
        self.key(0x1c)
        wait_for(lambda: self.check().u16(self.transport + 400) != sequence)
        self.wait_idle()
        if action == 'restart':
            for _ in range(3):
                self.key(0x1c)
                time.sleep(.3)


@contextmanager
def process(work, tag, report):
    config = work / 'observe.conf'
    config.write_text((work / 'dosbox.conf').read_text('ascii').replace(
        'webserver_enabled = off', 'webserver_enabled = on\nwebserver_bind_address = 127.0.0.1\nwebserver_port = 8136'), encoding='ascii')
    api = Api(8136)
    env = dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')
    with (work / (tag + '-stdout.log')).open('wb') as out, (work / (tag + '-stderr.log')).open('wb') as err:
        guest = subprocess.Popen([str(ROOT / 'mqa/dosbox/dosbox.exe'), '-conf', str(config), '-noconsole'],
            cwd=ROOT / 'mqa/dosbox', env=env, stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
        report.setdefault('processes', []).append(guest.pid)
        try:
            wait_for(lambda: api.get('dosbox/info'))
            def locate():
                data = api.get('memory/0/0x2000000')
                anchor = b'OVERFLOW\0UNDERFLOW\0TLOSS\0PLOSS'
                if data.count(MAGIC) == data.count(anchor) == 1:
                    return data.find(MAGIC), data.find(anchor) - 0x1082, data
            transport, ds, data = wait_for(locate)
            ldt = locate_ldt(data, ds, transport)
            yield Driver(api, transport, ds, ldt, work / 'RUS')
        except Exception:
            try:
                report['cpu'] = json.loads(api.get('cpu/state'))
                report['screen'] = text(api)
                (work / (tag + '-failure-memory.bin')).write_bytes(api.get('memory/0/0x2000000'))
            except OSError:
                pass
            raise
        finally:
            if guest.poll() is None:
                guest.terminate()
            guest.wait(timeout=10)


def run():
    work = ROOT / 'mqa' / ('save-native-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    prepare(work, ROOT / 'build/tools/nasm-2.16.03/nasm.exe', test_gates=True)
    report = {'work': str(work), 'status': 'running'}
    try:
        with process(work, 'first', report) as d:
            d.enter()
            report['exercise'] = observe(d.api, d.transport, 'ru', 1, exercise=True, step_door=True)
            raw = d.save()
            report['native_save_bytes'] = len(raw)
            m = d.check()
            health = m.u16(d.hero(m) + 0x5e)
            attempt = m.u16(d.ds + 0x1fae)
            for _ in range(3):
                d.load(health)
            report['same_process_loads'] = 3
            d.death('load')
            m = d.check()
            assert m.u16(d.hero(m) + 0x5e) == health
            assert m.u16(d.ds + 0x1fae) == attempt
            report['death_load'] = True
            d.death('restart', pending_save=True)
            assert (d.game / 'KRN1.SAV').read_bytes() == raw
            m = d.check()
            assert m.u16(d.ds + 0x1fae) == attempt + 1
            hud = m.pointer(d.ds + 0xa5d6, 0x2c)
            assert m.u16(hud + 0x1a) == m.u16(d.hero(m) + 0x72) == 0
            assert m.u16(hud + 0x26) == 0
            report['death_restart_preserves_save_and_attempt'] = True
            report['death_cancels_deferred_save_and_clears_hud'] = True
        with process(work, 'second', report) as d:
            report['early_choice_before_world'] = d.enter(choice='load')
            m = d.check()
            assert m.u16(d.hero(m) + 0x5e) == health
            assert m.u16(d.ds + 0x1fae) == attempt
            d.load(health)
            report['new_process_and_repeated_load'] = True
            latest = d.save()
            assert (d.game / 'KRN1.PRV').read_bytes() == raw
            report['previous_verified_generation'] = True
            broken = bytearray(latest)
            broken[-1] ^= 0x80
            (d.game / 'KRN1.SAV').write_bytes(broken)
            d.load(health)
            assert (d.game / 'KRN1.PRV').read_bytes() == raw
            report['corrupt_current_falls_back_to_previous'] = True
            # Restore the guest-created file for the fresh-start menu trial.
            (d.game / 'KRN1.SAV').write_bytes(latest)
        with process(work, 'third', report) as d:
            report['early_restart'] = d.enter(choice='restart')
            assert (d.game / 'KRN1.SAV').read_bytes() == latest
            m = d.check()
            assert m.u16(d.ds + 0x1fae) == 2
            report['early_restart_preserves_save'] = True
        report['status'] = 'passed'
    except Exception as exc:
        report['status'], report['error'] = 'failed', repr(exc)
        raise
    finally:
        (work / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(work, flush=True)
    return report


if __name__ == '__main__':
    result = run()
    print(json.dumps({key: value for key, value in result.items() if key != 'exercise'}, indent=2))
