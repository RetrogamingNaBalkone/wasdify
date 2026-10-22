"""Research restart of RUS level 1 without repeating native pickups on load.

This is a development harness, not a user-installable save/load patch. The
snapshot and results stay in mqa/ and carry an explicit experimental scope.
"""
import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import time

from inspect_kremlin_save_world import Memory, observe, pause_at_boundary, reposition_player, descriptor_base
from profile_kremlin import ROOT
from verify_kremlin_save_instances import native_call
from verify_kremlin_save_io import trial
from verify_kremlin_save_restart import inventory, bindings, resource_hashes, door_ids
from verify_kremlin_settings_dosbox import wait_for
from wasdify import kremlin_save_state as state_adapter
from wasdify import kremlin_save_doors as door_adapter
from wasdify import kremlin_save_events as event_adapter
from wasdify.kremlin_save import EventRecord


def world_address(memory, queue):
    ds = queue.object_count_address - 0x3e8
    return ds, memory.pointer(ds + 0x266a, 0x74a)


def frame_objects(memory, world):
    objects = {}
    for index in range(256):
        at = world + 0x30e + index * 4
        address = memory.pointer(at, 0x54)
        if address is not None:
            objects[index + 1] = event_adapter.NativeObject(
                index + 1, memory.u16(address + 0x12), address, memory.read(at, 4),
                memory.pointer(address + 4, 64), memory.u16(address + 2))
    return objects


def resources_at_start(memory, objects, queue):
    ds, _ = world_address(memory, queue)
    # Original wall layer, before row reversal/movement. Membership determines
    # which actor definitions native 1098:0EF7 actually positioned.
    placed = frozenset(value + 1 for value in memory.read(ds + 0x2d7e, 4096))
    return state_adapter.bind_resources(memory.read, memory.pointer, objects, placed_actors=placed)


def exercise_combat(api, transport, catalog, context):
    """Native shots cause damage and death; only the test's positioning is edited."""
    ds, world, ldt = context["ds"], context["world"], context["ldt"]
    pause_at_boundary(api, transport)
    try:
        memory = Memory(api.get("memory/0/0x2000000"), ldt)
        hero = next(obj for obj in catalog.baseline.values() if obj.native_type == 1)
        walls = memory.read(ds + 0x608a, 4096)
        floor = memory.read(ds + 0x718e, 4096)
        cell = memory.u16(world + 0x738)
        code = memory.data.find(b"KRSIO001")
        fixups, count = struct.unpack_from("<HH", memory.data, code + 10)
        delete_offset = next(struct.unpack_from("<HH", memory.data, code + fixups + i * 4)[0]
                             for i in range(count)
                             if struct.unpack_from("<HH", memory.data, code + fixups + i * 4)[1] == 5)
        world_code = memory.pointer(code + delete_offset) - 0x205
        if memory.read(world_code + 0x336, 1) != b"\xb8":
            raise ValueError("Native direction-table binding differs")
        selector = memory.u16(world_code + 0x337)
        table = descriptor_base(memory.read(memory.ldt + (selector & ~7), 8))
        directions = [(-struct.unpack("<i", memory.read(table + angle * 4, 4))[0],
                        struct.unpack("<i", memory.read(table + 0x5a4 + angle * 4, 4))[0])
                       for angle in range(360)]
        if not any(x or z for x, z in directions):
            raise ValueError("Native direction tables have not been initialized")
        candidates = []
        for key, obj in catalog.baseline.items():
            if obj.native_type != 9 or key not in catalog.placed_actors:
                continue
            health = struct.unpack("<h", memory.read(obj.address + 0xa4, 2))[0]
            if health <= 0:
                continue
            x, z = memory.u32(obj.address + 0x26), memory.u32(obj.address + 0x2e)
            for dx, dz in ((-2 * cell, 0), (2 * cell, 0), (0, -2 * cell), (0, 2 * cell)):
                nx, nz = x + dx, z + dz
                if not (0 <= nx < 64 * cell and 0 <= nz < 64 * cell):
                    continue
                tile = nz // cell * 64 + nx // cell
                middle = (z + dz // 2) // cell * 64 + (x + dx // 2) // cell
                floor_obj = catalog.baseline.get(floor[tile] + 1)
                if walls[tile] == walls[middle] == 32 and floor_obj and floor_obj.native_type == 0:
                    heading = max(range(360), key=lambda angle: -dx * directions[angle][0] - dz * directions[angle][1])
                    candidates.append((health, key, obj, nx, nz, heading, tile))
        if not candidates:
            raise ValueError("No placed enemy with an unobstructed native firing lane")
        health, victim_id, victim, x, z, heading, tile = min(candidates, key=lambda item: (item[0], item[1]))
        reposition_player(api, transport, ldt, ds, world, hero.address, x, z, heading)
    finally:
        api.put(str(transport + 46), b"\x01\0")
        wait_for(lambda: api.get(f"memory/{transport + 44}/2") == bytes(2))
    selected = struct.unpack("<h", api.get(f"memory/{hero.address + 0xce}/2"))[0]
    memory = Memory(api.get("memory/0/0x2000000"), ldt)
    weapon = memory.pointer(hero.address + 0xaa + selected * 4, 0x87)
    if weapon is None:
        raise ValueError("Native combat exercise has no selected weapon")
    wait_for(lambda: struct.unpack("<h", api.get(f"memory/{weapon + 0x83}/2"))[0] in (3, 7))
    energy_before = struct.unpack("<h", api.get(f"memory/{hero.address + 0x60}/2"))[0]
    keys = ds + 0x2155
    pending = struct.unpack("<H", api.get(f"memory/{keys - 3}/2"))[0]
    api.put(str(keys + 0x1d), b"\x03")
    api.put(str(keys - 3), struct.pack("<HB", (pending + 1) & 0xffff, 0x1d))
    try:
        end = time.monotonic() + 8
        while time.monotonic() < end:
            after = struct.unpack("<h", api.get(f"memory/{victim.address + 0xa4}/2"))[0]
            player_health = struct.unpack("<h", api.get(f"memory/{hero.address + 0x5e}/2"))[0]
            if player_health <= 0:
                raise ValueError("Player died during native combat exercise")
            if after <= 0:
                break
            time.sleep(.1)
        else:
            energy = struct.unpack("<h", api.get(f"memory/{hero.address + 0x60}/2"))[0]
            raise ValueError(f"Native shots did not kill enemy {victim_id}: health {health} -> {after}, "
                             f"energy {energy_before} -> {energy}, heading={heading}")
    finally:
        api.put(str(keys + 0x1d), b"\x02")
    energy_after = struct.unpack("<h", api.get(f"memory/{hero.address + 0x60}/2"))[0]
    if not energy_after < energy_before:
        raise ValueError("Combat did not consume native weapon energy")
    return {"victim_id": victim_id, "health_before": health, "health_after": after,
            "energy_before": energy_before, "energy_after": energy_after,
            "damage_and_death_performed_by_native_game": True}


def run(nasm, *, combat=False):
    work = ROOT / "mqa" / ("save-state-ru-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    work.mkdir(parents=True)
    artifact = work / "state.json"
    report = {"status": "running", "scope": "experimental-rus-level-1-state",
              "user_patch_implemented": False}
    reference_resources = resource_hashes()
    catalog = None
    source_context = {}

    def source_observer(api, transport, language, level):
        def baseline(memory, objects, queue, callbacks, now, doors):
            nonlocal catalog
            catalog = resources_at_start(memory, objects, queue)
            ds, world = world_address(memory, queue)
            source_context.update(ds=ds, world=world, ldt=memory.ldt)
            report["baseline_inventory"] = inventory(objects)
            report["baseline_bindings"] = bindings(objects, callbacks)
            events = event_adapter.capture(memory.read, objects, queue, callbacks, now)
            return (), events, doors, {"resources_bound_before_play": True}
        initial = observe(api, transport, language, level, components=baseline)
        if combat:
            observe(api, transport, language, level, True, step_door=True)
            report["combat"] = exercise_combat(api, transport, catalog, source_context)

        def capture(memory, objects, queue, callbacks, now, doors):
            ds, world = world_address(memory, queue)
            wall_ticks = memory.u32(transport + 52)
            state = state_adapter.capture(memory.read, objects, catalog, ds, world, wall_ticks)
            # Validate the complete proposed scalar/map plan before accepting an
            # artifact; unsupported fields/resources must fail at save time.
            state_adapter.plan_restore(state, memory.read, objects, catalog, ds, world, wall_ticks)
            events = event_adapter.capture(memory.read, objects, queue, callbacks, now)
            payload = {"experiment": "rus-level-1-state-v0", "resources": reference_resources,
                       "baseline_inventory": report["baseline_inventory"],
                       "state": {**state, "maps": [layer.hex() for layer in state["maps"]]},
                       "events": [asdict(event) for event in events],
                       "doors": [{"id": key, "state": asdict(saved)}
                                 for key, (_, saved) in sorted(door_ids(objects, doors).items())]}
            raw = json.dumps(payload, indent=2, sort_keys=True).encode("ascii")
            with artifact.open("xb") as stream:
                stream.write(raw)
            report["artifact_sha256"] = hashlib.sha256(raw).hexdigest()
            report["saved_bindings"] = bindings(objects, callbacks)
            hero = next(record for record in state["objects"] if record["type"] == 1)
            return (), events, doors, {"world_state_captured": True, "hero": hero,
                                      "objects": len(objects), "images": len(state["image_fractions"])}
        saved = observe(api, transport, language, level, True, components=capture, step_door=True)
        return {"initial": initial, "saved": saved}

    def target_observer(api, transport, language, level):
        context = {}

        def restore(memory, objects, queue, callbacks, now, doors):
            raw = artifact.read_bytes()
            if hashlib.sha256(raw).hexdigest() != report["artifact_sha256"]:
                raise ValueError("Research state artifact changed")
            payload = json.loads(raw)
            if payload["experiment"] != "rus-level-1-state-v0" or payload["resources"] != resource_hashes():
                raise ValueError("Research state/resources are incompatible")
            if inventory(objects) != payload["baseline_inventory"]:
                raise ValueError("Fresh native object inventory differs from baseline")
            resources = resources_at_start(memory, objects, queue)
            state = payload["state"]
            state["maps"] = [bytes.fromhex(layer) for layer in state["maps"]]
            ds, world = world_address(memory, queue)
            clones = []
            for record in state["objects"]:
                key = record["id"]
                if record["template"]:
                    if key in objects:
                        raise ValueError("Dynamic object would overwrite a baseline object")
                    error, _ = native_call(api, transport, 1, record["template"] - 1, key - 1)
                    if error:
                        raise ValueError(f"Native projectile reconstruction failed: {error}")
                    clones.append(key)
            memory = Memory(api.get("memory/0/0x2000000"), memory.ldt)
            objects.clear()
            objects.update(frame_objects(memory, world))
            wall_ticks = memory.u32(transport + 52)
            plan = state_adapter.plan_restore(state, memory.read, objects, resources, ds, world, wall_ticks)
            event_records = tuple(EventRecord(item["event_id"], item["owner_id"], item["event_type"],
                                  item["event_version"], {int(k): value for k, value in item["fields"].items()})
                                  for item in payload["events"])
            plan += event_adapter.plan_restore(event_records, memory.read, objects, queue, callbacks, now)
            by_id = door_ids(objects, doors)
            restored_doors = [(by_id[item["id"]][0], door_adapter.DoorState(**item["state"]))
                              for item in payload["doors"]]
            for binding, saved in restored_doors:
                plan += door_adapter.plan_restore(saved, memory.read, binding)
            hero = next(record for record in state["objects"] if record["type"] == 1)
            hero_obj = objects[hero["id"]]
            if memory.read(hero_obj.address + 0xaa, 36) != bytes(36):
                raise ValueError("Load scenario has already picked up weapons")
            report["restored_bindings"] = bindings(objects, callbacks)
            context.update(state=state, resources=resources, ds=ds, world=world, wall_ticks=wall_ticks)
            return plan, event_records, restored_doors, {
                "restored": True, "pickups_repeated_in_target": False, "native_clones": clones,
                "hero": hero, "objects": len(objects)}

        def verify(memory, objects):
            restored = state_adapter.capture(memory.read, objects, context["resources"],
                        context["ds"], context["world"], context["wall_ticks"])
            if restored != context["state"]:
                different = [key for key in restored if restored[key] != context["state"][key]]
                raise ValueError(f"World-state round trip differs: {different}")
            report["all_captured_fields_verified"] = True
        loaded = observe(api, transport, language, level, components=restore, verify_state=verify)
        def verify_continuation(memory, objects, queue, callbacks, now, doors):
            if combat:
                victim = objects[report["combat"]["victim_id"]]
                health = struct.unpack("<h", memory.read(victim.address + 0xa4, 2))[0]
                if health > 0:
                    raise ValueError("Killed enemy resurrected after restarting the game")
                report["dead_enemy_stayed_dead"] = True
            return (), event_adapter.capture(memory.read, objects, queue, callbacks, now), doors, {
                "native_simulation_continued": True}
        continued = observe(api, transport, language, level, components=verify_continuation)
        if not continued["event_adapter_round_trip"]:
            raise ValueError("Native game did not continue with a valid event queue")
        return {"loaded": loaded, "continued": continued}

    try:
        report["source"] = trial("ru", nasm, observer=source_observer, world_research=True)
        report["target"] = trial("ru", nasm, observer=target_observer, world_research=True, data_size=12288)
        if reference_resources != resource_hashes():
            raise ValueError("Reference resources changed")
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        (work / "report.json").write_text(json.dumps(report, indent=2), encoding="ascii")
        print(work, flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nasm", type=Path, default=ROOT / "build/tools/nasm-2.16.03/nasm.exe")
    parser.add_argument("--combat", action="store_true")
    args = parser.parse_args()
    run(args.nasm, combat=args.combat)
