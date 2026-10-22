"""Check the pre-splash choice in disposable RUS processes.

Only this research driver supplies automatic input. The playable launcher does
not navigate menus. Originals and the user's save are read, never modified.
"""
from datetime import datetime
import json
import shutil
import struct
import threading
import time

from inspect_kremlin_save_world import Memory, pause_at_boundary
from play_kremlin_save import Session
from profile_kremlin import ROOT, capture
from verify_kremlin_save_io import trial
from verify_kremlin_settings_dosbox import wait_for
from wasdify.i18n import Translator


def run():
    work = ROOT / 'mqa' / ('save-entry-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    work.mkdir()
    report = {}
    for target in ('none', 'restart', 'load'):
        def observer(api, transport, language, level):
            saved = work / (target + '.PROTO')
            if target != 'none':
                shutil.copyfile(ROOT / 'mqa/save-play-ru/KRS1.PROTO', saved)
            session = Session(api, transport, Translator('ru'), saved)
            original_get = api.get
            stop = threading.Event()
            phases, draws = {}, []
            result = {}

            def view():
                return Memory(original_get('memory/0/0x2000000'), session.ldt)

            def frame(name):
                m = view()
                capture(api, {'buffer_base': m.pointer(session.ds + 0xb53a, 1)},
                        work / (target + '-' + name + '.bmp'))

            def key(scan):
                at = session.ds + 0x2152
                count = struct.unpack('<H', original_get(f'memory/{at}/2'))[0]
                api.put(str(at + 3 + scan), b'\x03')
                api.put(str(at), struct.pack('<HB', (count + 1) & 65535, scan))
                time.sleep(.08)
                api.put(str(at + 3 + scan), b'\x02')

            def navigate():
                # Wait for the bridge to resolve native data, then drive only
                # vanilla menus and the requested vanilla introduction.
                try:
                    while not stop.is_set():
                        if not hasattr(session, 'ds'):
                            time.sleep(.01)
                            continue
                        gate = original_get(f'memory/{transport + 44}/2')
                        if gate == b'\x01\0':
                            return
                        request = original_get(f'memory/{transport + 16}/2')
                        enabled = original_get(f'memory/{transport + 156}/2')
                        if gate == bytes(2) and (enabled == b'\x01\0' or request == b'\x11\0'):
                            key(0x1c)
                        time.sleep(.3)
                except OSError:
                    if not stop.is_set():
                        raise

            def get(path):
                if draws:
                    scan = 0x48 if target == 'restart' and len(draws) == 1 else 0x1c
                    if path == f'memory/{session.ds + 0x2155 + scan}/1':
                        phase = phases.get(scan, 0)
                        if phase < 2:
                            api.put(str(session.ds + 0x2155 + scan), bytes((3 if phase == 0 else 2,)))
                            phases[scan] = phase + 1
                return original_get(path)

            original_draw = session.draw_dialog

            def draw(text):
                original_draw(text)
                draws.append(text)
                if text.startswith(session.game_messages['choice_title']):
                    m = view()
                    assert m.pointer(session.ds + 0x266a) is None, 'world exists before early choice'
                    assert m.u16(session.ds + 0x1fae) == 1
                    assert m.u16(transport + 44) == 3
                    frame('before-world-' + str(len(draws)))
                    result['choice_before_world_and_attempt'] = True

            session.draw_dialog, api.get = draw, get
            driver = threading.Thread(target=navigate, daemon=True)
            driver.start()
            try:
                action = session.start_from_selection()
                assert action == (None if target == 'none' else target)
                result['action'] = action
                m = view()
                assert m.u16(transport + 44) == 0
                assert m.u16(session.ds + 0x1fae) == 2
                if target == 'none':
                    assert not draws
                frame('playing')
            finally:
                stop.set()
                driver.join(2)
                api.get, session.draw_dialog = original_get, original_draw

            if target == 'load':
                for _ in range(2):
                    session.load(saved)
                result['repeat_loads'] = 2
                pause_at_boundary(api, transport)
                m = view()
                hero = m.pointer(session.world + 0x442, 0xde)
                api.put(str(hero + 0x5e), bytes(2))
                session.release_gate()
                wait_for(lambda: original_get(f'memory/{transport + 146}/2') == b'\x01\0')
                phases.clear()
                draws.clear()

                def death_draw(text):
                    original_draw(text)
                    draws.append(text)

                # Select START, then observe the real native attempt overlay
                # before sending its confirmation from this test driver.
                target_for_menu = 'restart'

                def death_get(path):
                    scan = 0x48 if len(draws) == 1 else 0x1c
                    if path == f'memory/{session.ds + 0x2155 + scan}/1':
                        phase = phases.get(scan, 0)
                        if phase < 2:
                            api.put(str(session.ds + 0x2155 + scan), bytes((3 if phase == 0 else 2,)))
                            phases[scan] = phase + 1
                    return original_get(path)

                session.draw_dialog, api.get = death_draw, death_get
                try:
                    assert session.choose_level_action(death=True) == target_for_menu
                finally:
                    session.draw_dialog, api.get = original_draw, original_get
                time.sleep(1)
                m = view()
                bp = m.u16(transport + 40)
                assert m.u16(session.ds + bp - 0x3a) == 6
                assert m.u16(session.ds + 0x1fae) == 2
                frame('attempt-2')
                key(0x1c)
                wait_for(lambda: struct.unpack('<H', original_get(f'memory/{session.ds + 0x1fae}/2'))[0] == 3)
                result['death_restart_native_attempt_2'] = True
            result['session'] = session.reports
            report[target] = result
            return result

        trial('ru', ROOT / 'build/tools/nasm-2.16.03/nasm.exe', observer=observer,
              world_research=True, play_mode=True)
    (work / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(work, flush=True)


if __name__ == '__main__':
    run()
