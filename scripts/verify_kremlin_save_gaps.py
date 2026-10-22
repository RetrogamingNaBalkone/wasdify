"""Exercise level-1 state gaps on disposable RUS copies, across two processes.

Player input/position and one unused actor's initial placement are controlled.
The unused actor is positioned as in native 1098:0EF7; its activation is never
written by the harness. Floor animation, AI,
projectile allocation, destruction and reuse execute in the original game.
Full reports and temporary snapshots belong in ignored mqa/.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import struct
import time

from profile_kremlin import ROOT
from play_kremlin_save import Session, decode
from inspect_kremlin_save_world import Memory, observe, pause_at_boundary, reposition_player, step_at_boundary
from verify_kremlin_save_state import frame_objects
from verify_kremlin_save_io import trial
from wasdify.i18n import Translator


def run(nasm):
    work = ROOT / "mqa" / ("save-gaps-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    work.mkdir()
    snapshot = work / "KRS1.PROTO"
    report = {"status": "running", "user_patch_implemented": False}

    def bind(api, transport):
        api.put(str(transport + 156), bytes(2))
        api.put(str(transport + 16), struct.pack("<H", 17))
        session = Session(api, transport, Translator("ru"), snapshot)
        session.bind(startup=True)
        return session

    def source(api, transport, language, level):
        session = bind(api, transport)
        session.release_gate()
        observe(api, transport, language, level, exercise=True, step_door=True)
        pause_at_boundary(api, transport)
        view = Memory(api.get("memory/0/0x2000000"), session.ldt)
        objects = frame_objects(view, session.world)
        hero = next(obj for obj in objects.values() if obj.native_type == 1)
        cell = view.u16(session.world + 0x738)
        walls = view.read(session.ds + 0x608a, 4096)
        surfaces = [view.read(session.ds + offset, 4096) for offset in (0x718e, 0x8292)]
        candidates = sorted({tile for surface in surfaces for tile, value in enumerate(surface)
                             if objects[value + 1].native_type == 12 and walls[tile] == 32})
        if not candidates:
            raise ValueError("No unobstructed native floor effect")
        report["floor_candidates"] = candidates
        # Keep firing through normal native input until an observed retired
        # projectile slot becomes a live shot again. Pointer equality alone
        # would miss allocators that reuse the same memory block.
        keys = session.ds + 0x2155
        api.put(str(keys + 0x1d), b"\x03")
        api.put(str(keys - 3), struct.pack("<HB", (view.u16(keys-3)+1) & 0xffff, 0x1d))
        session.release_gate()
        retired, reused = set(), set()
        deadline = time.monotonic() + 12
        try:
            while time.monotonic() < deadline:
                pause_at_boundary(api, transport)
                view = Memory(api.get("memory/0/0x2000000"), session.ldt)
                current = frame_objects(view, session.world)
                for key, obj in current.items():
                    if obj.native_type != 8:
                        continue
                    flags = view.u16(obj.address + 0x15)
                    if flags & 0x400:
                        retired.add(key)
                    if key in retired and flags & 0x1000:
                        reused.add(key)
                session.release_gate()
                if reused:
                    break
                time.sleep(.03)
            if not reused:
                raise ValueError(f"No native projectile slot reuse observed; retired={sorted(retired)}")
        finally:
            session.clear_keyboard()
        report["native_reused_slots"] = sorted(reused)
        session.save(work / "REUSE.PROTO")
        reuse = decode((work / "REUSE.PROTO").read_bytes())
        report["saved_reused_slots"] = [r["id"] for r in reuse["state"]["objects"]
                                         if r["id"] in reused and r["template"]]
        if not report["saved_reused_slots"]:
            raise ValueError("Reused native slot was not serialized as an instance")
        pause_at_boundary(api, transport)
        view = Memory(api.get("memory/0/0x2000000"), session.ldt)
        walls = view.read(session.ds + 0x608a, 4096)
        hero_tile = view.u32(hero.address + 0x2e) // cell * 64 + view.u32(hero.address + 0x26) // cell
        nearby = [hero_tile + delta for delta in (-2, 2, -128, 128)
                  if 0 <= hero_tile + delta < 4096
                  and abs((hero_tile + delta) % 64 - hero_tile % 64) <= 2
                  and walls[hero_tile + delta] == walls[hero_tile + delta//2] == 32]
        if not nearby:
            raise ValueError("No clear test placement for an unused actor")
        key, actor = max((key, obj) for key, obj in objects.items()
                         if obj.native_type == 9 and key not in session.resources.placed_actors)
        tile = nearby[0]
        height = struct.unpack("<h", view.read(actor.address + 0x19, 2))[0]
        # 1098:0EF7 writes position and both vertical bounds only when an
        # actor occurs in the initial wall layer. Supply that placement for
        # this controlled counterexample, leaving AI state/counters/events
        # entirely to the native renderer and activation method 1068:3747.
        for offset, wire, value in ((0x26, "i", tile % 64 * cell + cell//2),
                                    (0x2a, "i", -height), (0x2e, "i", tile//64 * cell + cell//2),
                                    (0x9e, "h", -height), (0xa0, "h", -height)):
            api.put(str(actor.address + offset), struct.pack("<" + wire, value))
        api.put(str(session.ds + 0x608a + tile), bytes((key-1,)), expected=b" ")
        assert view.u16(actor.address + 0xa2) == 0
        activated = False
        for angle in range(0, 360, 30):
            api.put(str(hero.address + 0x32), struct.pack("<h", angle))
            step_at_boundary(api, transport)
            view = Memory(api.get("memory/0/0x2000000"), session.ldt)
            if view.u16(actor.address + 0xa2):
                activated = True
                break
        if not activated:
            raise ValueError("Native renderer did not activate the newly placed actor")
        session.release_gate()
        session.save(work / "UNUSED.PROTO")
        saved_actor = next(r for r in decode((work / "UNUSED.PROTO").read_bytes())["state"]["objects"]
                           if r["id"] == key)
        if not saved_actor["fields"]["ai_state"] or "x" not in saved_actor["fields"]:
            raise ValueError("Activated unused actor lost its live state")
        report["unused_actor"] = {"id": key, "native_activation": True,
                                  "test_placement_only": True, "fields": saved_actor["fields"]}
        found = None
        positions = sorted({near for tile in candidates for near in (tile-1, tile+1, tile-64, tile+64)
                            if 0 <= near < 4096 and abs(near % 64 - tile % 64) <= 1
                            and near not in candidates and walls[near] == 32})
        for tile in positions:
            pause_at_boundary(api, transport)
            reposition_player(api, transport, session.ldt, session.ds, session.world, hero.address,
                              (tile % 64) * cell + cell // 2, (tile // 64) * cell + cell // 2)
            for angle in range(0, 360, 30):
                api.put(str(hero.address + 0x32), struct.pack("<h", angle))
                step_at_boundary(api, transport)
                view = Memory(api.get("memory/0/0x2000000"), session.ldt)
                active = [(key, view.u16(obj.address + 0x50)) for key, obj in objects.items()
                          if obj.native_type == 12 and view.u16(obj.address + 0x50) < 18]
                if active:
                    found = active[0]
                    break
            if found:
                break
            session.release_gate()
        if found is None:
            raise ValueError("Native rendering never started a floor effect")
        report["floor_before_save"] = found
        # Save performs one normal frame to paint its progress message. The
        # native effect must still be in progress in the resulting file.
        session.release_gate()
        session.save(snapshot)
        saved = decode(snapshot.read_bytes())
        phases = {r["id"]: r["fields"]["frame"] for r in saved["state"]["objects"] if r["type"] == 12}
        if not any(0 < phase < 18 for phase in phases.values()):
            raise ValueError(f"Floor effect was idle at save time: {phases}")
        report["saved_floor_phases"] = phases
        return {"session": session.reports}

    def target(api, transport, language, level):
        session = bind(api, transport)
        session.load(work / "UNUSED.PROTO")
        time.sleep(.3)
        pause_at_boundary(api, transport)
        view = Memory(api.get("memory/0/0x2000000"), session.ldt)
        actor = frame_objects(view, session.world)[report["unused_actor"]["id"]]
        if not view.u16(actor.address + 0xa2):
            raise ValueError("Activated unused actor became dormant after loading")
        report["unused_actor"]["continued_ai_state"] = view.u16(actor.address + 0xa2)
        continued = {"x": view.u32(actor.address + 0x26), "z": view.u32(actor.address + 0x2e),
                     "frame": view.u16(actor.address + 0x50),
                     "stride_count": view.u16(actor.address + 0xac)}
        if all(value == report["unused_actor"]["fields"][name] for name, value in continued.items()):
            raise ValueError("Activated unused actor did not advance after loading")
        report["unused_actor"]["continued_fields"] = continued
        session.release_gate()
        session.load(work / "REUSE.PROTO")
        report["reused_slots_loaded_in_new_process"] = True
        observe(api, transport, language, level, dismiss_intro=False,
                runtime_layout=(session.ds, session.ldt))
        session.load(snapshot)
        # load() verifies every captured field before releasing the gate.
        saved = decode(snapshot.read_bytes())
        wanted = {r["id"]: r["fields"]["frame"] for r in saved["state"]["objects"]
                  if r["type"] == 12 and r["fields"]["frame"] < 18}
        deadline = time.monotonic() + 5
        observed = {}
        while time.monotonic() < deadline:
            pause_at_boundary(api, transport)
            view = Memory(api.get("memory/0/0x2000000"), session.ldt)
            objects = frame_objects(view, session.world)
            observed = {key: view.u16(objects[key].address + 0x50) for key in wanted}
            session.release_gate()
            if any(observed[key] != phase for key, phase in wanted.items()):
                break
            time.sleep(.05)
        else:
            raise ValueError("Restored floor effect did not continue")
        report["continued_floor_phases"] = observed
        session.load(snapshot)
        return {"session": session.reports, "repeated_load_verified": True}

    try:
        first = trial("ru", nasm, observer=source, world_research=True, play_mode=True)
        second = trial("ru", nasm, observer=target, world_research=True, play_mode=True, data_size=12288)
        report.update(status="passed", source=first["work"], target=second["work"],
                      process_ids=[first["process_id"], second["process_id"]])
    except Exception as exc:
        report.update(status="failed", error=repr(exc))
        raise
    finally:
        (work / "report.json").write_text(json.dumps(report, indent=2), encoding="ascii")
        print(work, flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nasm", type=Path, default=ROOT / "build/tools/nasm-2.16.03/nasm.exe")
    run(parser.parse_args().nasm)
