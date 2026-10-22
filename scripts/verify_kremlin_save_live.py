"""Verify live reload and the two level choices in an isolated RUS process.

Game files and screenshots stay in ignored mqa/. No original is changed.
Keyboard release states are injected through the researched native IRQ table;
the real game handles death, object destruction/construction and rendering.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import struct
import time

from profile_kremlin import ROOT, capture
from inspect_kremlin_save_world import Memory, observe, pause_at_boundary
from play_kremlin_save import Session
from verify_kremlin_save_io import trial
from verify_kremlin_settings_dosbox import wait_for
from wasdify.i18n import Translator


def run(nasm, *, visible=False):
    work = ROOT / 'mqa' / ('save-live-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    work.mkdir()
    report = {}

    def observer(api, transport, language, level):
        # Component tests below operate on an already entered level. The
        # production pre-splash flow is covered by verify_kremlin_save_entry.
        api.put(str(transport + 156), bytes(2))
        api.put(str(transport + 16), struct.pack('<H', 17))
        saved = work / 'KRS1.PROTO'
        session = Session(api, transport, Translator('ru'), saved)
        session.bind(startup=True)
        api.game_base = session.ds
        assert session.live
        assert api.get(f'memory/{transport + 44}/2') == b'\x01\0'
        assert api.get(f'memory/{transport + 16}/2') == bytes(2)
        assert session.choose_level_action() is None

        def memory():
            return Memory(api.get('memory/0/0x2000000'), session.ldt)

        def frame(name):
            view = memory()
            capture(api, {'buffer_base': view.pointer(session.ds + 0xb53a, 1)}, work / (name + '.bmp'))

        def kill():
            pause_at_boundary(api, transport)
            view = memory()
            hero = view.pointer(session.world + 0x442, 0xde)
            before = view.u16(hero + 0xd6)
            api.put(str(hero + 0x5e), bytes(2), expected=view.read(hero + 0x5e, 2))
            session.release_gate()
            return hero, before

        # With no save the hook must not interrupt the native life decrement.
        observe(api, transport, language, level)
        hero, lives = kill()
        wait_for(lambda: struct.unpack('<H', api.get(f'memory/{hero + 0xd6}/2'))[0] == lives - 1)
        assert api.get(f'memory/{transport + 146}/2') == bytes(2)
        report['no_save_native_respawn'] = True

        observe(api, transport, language, level, exercise=True, step_door=True)
        session.save(saved)
        for _ in range(3):
            session.load(saved)
        report['same_process_reloads'] = 3

        def choose(target, death, tag):
            original_get, original_draw = api.get, session.draw_dialog
            ready, draws = False, 0
            phases = {}

            def draw(text):
                nonlocal ready, draws
                original_draw(text)
                frame(f'{tag}-{draws}')
                draws += 1
                ready = True

            def get(path):
                if ready:
                    scan = 0x48 if target == 'restart' and draws < 2 else 0x1c
                    if path == f'memory/{session.ds + 0x2155 + scan}/1':
                        phase = phases.get(scan, 0)
                        if phase < 2:
                            api.put(str(session.ds + 0x2155 + scan), bytes((3 if phase == 0 else 2,)))
                            phases[scan] = phase + 1
                return original_get(path)

            api.get, session.draw_dialog = get, draw
            try:
                result = session.choose_level_action(death=death)
                assert result == target
            finally:
                api.get, session.draw_dialog = original_get, original_draw
            if target == 'restart':
                observe(api, transport, language, level)
            return result

        report['startup_load'] = choose('load', False, 'startup-load')
        report['startup_restart'] = choose('restart', False, 'startup-restart')
        # A real stretch of healthy play must not produce a second startup
        # question or stop at an unclaimed gate left over from binding.
        api.put(str(session.ds + 0x2155 + 0x48), b'\x03')
        samples = []
        for _ in range(40):
            time.sleep(.1)
            view = memory()
            hero = view.pointer(session.world + 0x442, 0xde)
            assert view.u16(hero + 0x5e) > 0
            assert view.u16(transport + 44) == 0
            assert session.request() is None
            samples.append(view.read(hero + 0x26, 12).hex())
        session.clear_keyboard()
        assert len(set(samples)) > 1, 'healthy play did not advance'
        report['healthy_play_without_choice_seconds'] = 4
        for target in ('load', 'restart'):
            hero, lives = kill()
            wait_for(lambda: api.get(f'memory/{transport + 146}/2') == b'\x01\0')
            # The choice appears before vanilla life decrement.
            assert struct.unpack('<H', api.get(f'memory/{hero + 0xd6}/2'))[0] == lives
            report['death_' + target] = choose(target, True, 'death-' + target)
            observe(api, transport, language, level, dismiss_intro=False,
                    runtime_layout=(session.ds, session.ldt))
        report['session'] = session.reports
        return report

    try:
        result = trial('ru', nasm, observer=observer, world_research=True, interactive=visible,
                       play_mode=True)
        report['process_id'] = result['process_id']

        def startup_observer(api, transport, language, level):
            api.put(str(transport + 156), bytes(2))
            api.put(str(transport + 16), struct.pack('<H', 17))
            session = Session(api, transport, Translator('ru'), work / 'KRS1.PROTO')
            session.bind(startup=True)
            original_draw, original_get = session.draw_dialog, api.get
            enter_phase = 0

            def get(path):
                nonlocal enter_phase
                if path == f'memory/{session.ds + 0x2155 + 0x1c}/1' and enter_phase < 2:
                    api.put(str(session.ds + 0x2155 + 0x1c), bytes((3 if enter_phase == 0 else 2,)))
                    enter_phase += 1
                return original_get(path)

            def draw(text):
                original_draw(text)
                if text.startswith(session.game_messages['choice_title']):
                    view = Memory(api.get('memory/0/0x2000000'), session.ldt)
                    buffer = view.pointer(session.ds + 0xb53a, 1)
                    assert len(set(view.read(buffer, 640 * 480))) > 1, 'startup menu is blank'
                    capture(api, {'buffer_base': buffer}, work / 'first-frame-choice.bmp')
                    hero = view.pointer(session.world + 0x442, 0xde)
                    before = view.read(hero + 0x26, 12)
                    time.sleep(.3)
                    assert api.get(f'memory/{hero + 0x26}/12') == before
                    assert api.get(f'memory/{transport + 44}/2') == b'\x01\0'
                    api.get = get

            session.draw_dialog = draw
            assert session.choose_level_action() == 'load'
            assert api.get(f'memory/{transport + 146}/2') == bytes(2)
            return {'first_frame_choice_visible_and_paused': True, 'session': session.reports}

        startup = trial('ru', nasm, observer=startup_observer, world_research=True,
                        interactive=visible, play_mode=True)
        report['first_frame_with_save'] = startup['world']
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        raise
    finally:
        (work / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(work, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nasm', type=Path,
                        default=ROOT / 'build/tools/nasm-2.16.03/nasm.exe')
    parser.add_argument('--visible', action='store_true')
    args = parser.parse_args()
    run(args.nasm, visible=args.visible)
