"""Native-only continuation of slot reuse, newly activated AI and surface phase.

Only controlled positions and player input are provided by the host. Every
capture, disk operation and restoration is performed by the autonomous payload.
"""
from datetime import datetime
import json
import struct
import time

from prepare_kremlin_save_native import prepare, ROOT
from verify_kremlin_save_native import process, records
from inspect_kremlin_save_world import observe, pause_at_boundary, step_at_boundary, reposition_player
from verify_kremlin_save_state import frame_objects
from verify_kremlin_settings_dosbox import wait_for


def run():
    work = ROOT / 'mqa' / ('save-native-gaps-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    prepare(work, ROOT / 'build/tools/nasm-2.16.03/nasm.exe', test_gates=True)
    report = {'status': 'running', 'scope': 'rus-level-1'}
    try:
        with process(work, 'source', report) as d:
            d.enter()
            observe(d.api, d.transport, 'ru', 1, exercise=True, step_door=True)
            pause_at_boundary(d.api, d.transport)
            m = d.check()
            world = m.pointer(d.ds + 0x266a, 0x74a)
            objects = frame_objects(m, world)
            hero = d.hero(m)
            cell = m.u16(world + 0x738)
            placed = frozenset(value + 1 for value in m.read(d.ds + 0x2d7e, 4096))
            walls = m.read(d.ds + 0x608a, 4096)
            candidates = sorted({tile for offset in (0x718e, 0x8292)
                for tile, value in enumerate(m.read(d.ds + offset, 4096))
                if objects[value + 1].native_type == 12 and walls[tile] == 32})
            assert candidates
            keys = d.ds + 0x2155
            d.api.put(str(keys + 0x1d), b'\x03')
            d.api.put(str(keys - 3), struct.pack('<HB', (m.u16(keys - 3) + 1) & 65535, 0x1d))
            d.release()
            retired, reused = set(), set()
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                pause_at_boundary(d.api, d.transport)
                m = d.check()
                for key, obj in frame_objects(m, world).items():
                    if obj.native_type == 8:
                        flags = m.u16(obj.address + 0x15)
                        if flags & 0x400:
                            retired.add(key)
                        if key in retired and flags & 0x1000:
                            reused.add(key)
                d.release()
                if reused:
                    break
                time.sleep(.03)
            assert reused, retired
            d.api.put(str(keys + 0x1d), b'\x02')
            reuse = d.save()
            saved_reuse = [key for key, record in records(reuse).items() if key in reused and record['template']]
            assert saved_reuse
            (work / 'REUSE.KRN').write_bytes(reuse)
            report['reused_projectile_slots'] = saved_reuse

            pause_at_boundary(d.api, d.transport)
            m = d.check()
            walls = m.read(d.ds + 0x608a, 4096)
            hero_tile = m.u32(hero + 0x2e) // cell * 64 + m.u32(hero + 0x26) // cell
            nearby = [hero_tile + delta for delta in (-2, 2, -128, 128)
                if 0 <= hero_tile + delta < 4096 and abs((hero_tile + delta) % 64 - hero_tile % 64) <= 2
                and walls[hero_tile + delta] == walls[hero_tile + delta // 2] == 32]
            assert nearby
            actor_id, actor = max((key, obj) for key, obj in objects.items() if obj.native_type == 9 and key not in placed)
            tile = nearby[0]
            height = struct.unpack('<h', m.read(actor.address + 0x19, 2))[0]
            for offset, wire, value in ((0x26, 'i', tile % 64 * cell + cell // 2), (0x2a, 'i', -height),
                    (0x2e, 'i', tile // 64 * cell + cell // 2), (0x9e, 'h', -height), (0xa0, 'h', -height)):
                d.api.put(str(actor.address + offset), struct.pack('<' + wire, value))
            d.api.put(str(d.ds + 0x608a + tile), bytes((actor_id - 1,)), expected=b' ')
            assert m.u16(actor.address + 0xa2) == 0
            for angle in range(0, 360, 30):
                d.api.put(str(hero + 0x32), struct.pack('<h', angle))
                step_at_boundary(d.api, d.transport)
                if d.check().u16(actor.address + 0xa2):
                    break
            else:
                raise ValueError('native_ai_did_not_activate')
            d.release()
            unused = d.save()
            saved_actor = records(unused)[actor_id]['fields']
            assert saved_actor['ai_state'] and 'x' in saved_actor
            (work / 'UNUSED.KRN').write_bytes(unused)
            report['activated_ai'] = actor_id

            positions = sorted({near for tile in candidates for near in (tile - 1, tile + 1, tile - 64, tile + 64)
                if 0 <= near < 4096 and abs(near % 64 - tile % 64) <= 1 and near not in candidates and walls[near] == 32})
            found = None
            for tile in positions:
                pause_at_boundary(d.api, d.transport)
                reposition_player(d.api, d.transport, d.ldt, d.ds, world, hero,
                    tile % 64 * cell + cell // 2, tile // 64 * cell + cell // 2)
                for angle in range(0, 360, 30):
                    d.api.put(str(hero + 0x32), struct.pack('<h', angle))
                    step_at_boundary(d.api, d.transport)
                    m = d.check()
                    active = [(key, m.u16(obj.address + 0x50)) for key, obj in objects.items()
                        if obj.native_type == 12 and m.u16(obj.address + 0x50) < 18]
                    if active:
                        found = active[0]
                        break
                d.release()
                if found:
                    break
            assert found
            floor = d.save()
            phases = {key: r['fields']['frame'] for key, r in records(floor).items()
                if r['type'] == 12 and 0 < r['fields']['frame'] < 18}
            assert phases
            (work / 'FLOOR.KRN').write_bytes(floor)
            report['saved_surface_phases'] = phases

        with process(work, 'target', report) as d:
            d.enter(choice='load')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                pause_at_boundary(d.api, d.transport)
                m = d.check()
                world = m.pointer(d.ds + 0x266a, 0x74a)
                current = frame_objects(m, world)
                advanced = {key: m.u16(current[key].address + 0x50) for key in phases}
                d.release()
                if any(advanced[key] != phases[key] for key in phases):
                    break
                time.sleep(.05)
            else:
                raise ValueError('native_surface_did_not_continue')
            report['continued_surface_phases'] = advanced
            (d.game / 'KRN1.SAV').write_bytes(unused)
            health = next(r['fields']['health'] for r in records(unused).values() if r['type'] == 1)
            d.load(health, check_health=False)
            time.sleep(.3)
            pause_at_boundary(d.api, d.transport)
            m = d.check()
            actor = frame_objects(m, world)[actor_id]
            assert m.u16(actor.address + 0xa2)
            continued = {'x': m.u32(actor.address + 0x26), 'z': m.u32(actor.address + 0x2e),
                'frame': m.u16(actor.address + 0x50), 'stride_count': m.u16(actor.address + 0xac)}
            assert any(value != saved_actor[name] for name, value in continued.items())
            report['continued_ai'] = continued
            d.release()
            (d.game / 'KRN1.SAV').write_bytes(reuse)
            health = next(r['fields']['health'] for r in records(reuse).values() if r['type'] == 1)
            d.load(health, check_health=False)
            observe(d.api, d.transport, 'ru', 1, dismiss_intro=False, runtime_layout=(d.ds, d.ldt))
            report['reused_slot_queue_verified'] = True
            (d.game / 'KRN1.SAV').write_bytes(floor)
            health = next(r['fields']['health'] for r in records(floor).values() if r['type'] == 1)
            d.load(health, check_health=False)
            report['repeated_native_load'] = True
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=repr(exc))
        raise
    finally:
        (work / 'report.json').write_text(json.dumps(report, indent=2), encoding='ascii')
        print(work, flush=True)
    return report


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
