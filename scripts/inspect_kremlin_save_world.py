"""Measure world/object/event ownership at a verified native frame boundary.

Uses disposable copies and the KRS disk-transport research trampoline. The
result is an analysis inventory, NOT a saved game or a process-memory snapshot.
"""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
import struct
import time
from pathlib import Path

from profile_kremlin import ROOT
from verify_kremlin_save_io import trial
from verify_kremlin_settings_dosbox import wait_for
from wasdify import kremlin_save_events as events_adapter
from wasdify import kremlin_save_doors as door_adapter


def descriptor_base(data):
    return int.from_bytes(data[2:4], "little") | data[4] << 16 | data[7] << 24


def locate_ldt(memory, native_base, transport_base):
    tables = set()
    for access in (0x82, 0xa2, 0xc2, 0xe2):
        position = 5
        while True:
            position = memory.find(bytes((access,)), position)
            if position < 0:
                break
            descriptor = memory[position - 5:position + 3]
            position += 1
            if len(descriptor) != 8 or descriptor[6] & 0x80:
                continue
            limit = int.from_bytes(descriptor[:2], "little") | (descriptor[6] & 15) << 16
            table = descriptor_base(descriptor)
            if not (0x100 <= limit < 65536 and 0 < table < len(memory) - limit):
                continue
            bases = {descriptor_base(memory[i:i + 8]) for i in range(table, table + limit - 6, 8)}
            if native_base in bases and transport_base in bases:
                tables.add(table)
    if len(tables) != 1:
        raise ValueError(f"Ambiguous LDT candidates: {tables}")
    return tables.pop()


class Memory:
    def __init__(self, data, ldt):
        self.data, self.ldt = data, ldt

    def u16(self, address):
        return struct.unpack_from("<H", self.data, address)[0]

    def read(self, address, size):
        return self.data[address:address + size]

    def u32(self, address):
        return struct.unpack_from("<I", self.data, address)[0]

    def pointer(self, address, size=1):
        offset, selector = struct.unpack_from("<HH", self.data, address)
        if offset == selector == 0:
            return None
        if selector & 4 == 0:
            raise ValueError("Expected an LDT selector")
        descriptor = self.data[self.ldt + (selector & ~7):self.ldt + (selector & ~7) + 8]
        limit = int.from_bytes(descriptor[:2], "little") | (descriptor[6] & 15) << 16
        if descriptor[6] & 0x80:
            limit = (limit << 12) | 0xfff
        if descriptor[5] & 0x90 != 0x90 or offset + size - 1 > limit:
            raise ValueError("Invalid protected pointer")
        result = descriptor_base(descriptor) + offset
        if result + size > len(self.data):
            raise ValueError("Pointer outside observed memory")
        return result


def pause_at_boundary(api, transport, *, allow_death=False):
    api.put(str(transport + 46), bytes(2))
    api.put(str(transport + 16), struct.pack("<H", 13))
    try:
        def held():
            state = api.get(f"memory/{transport + 44}/2")
            return state if state == b"\x01\0" or allow_death and state == b"\x02\0" else None
        state = wait_for(held, timeout=20, poll_interval=.005)
        if state == b"\x02\0" and api.get(f"memory/{transport + 16}/2") == b"\x0d\0":
            api.put(str(transport + 16), bytes(2), expected=b"\x0d\0")
        return state
    except TimeoutError:
        if api.get(f"memory/{transport + 16}/2") == b"\x0d\0":
            api.put(str(transport + 16), bytes(2), expected=b"\x0d\0")
        if api.get(f"memory/{transport + 44}/2") == b"\x01\0":
            api.put(str(transport + 46), b"\x01\0")
        raise


def step_at_boundary(api, transport):
    """Execute exactly one native main-loop iteration, then stop again."""
    done = api.get(f"memory/{transport + 18}/2")
    api.put(str(transport + 16), struct.pack("<H", 13))
    api.put(str(transport + 46), b"\x01\0")
    wait_for(lambda: api.get(f"memory/{transport + 18}/2") != done
             and api.get(f"memory/{transport + 44}/2") == b"\x01\0", timeout=20)


def reposition_player(api, transport, ldt, ds, world, player, x, z, heading=None):
    """Test positioning with the native map/door bookkeeping (1068:0EB1)."""
    owns_gate = api.get(f"memory/{transport + 44}/2") != b"\x01\0"
    if owns_gate:
        pause_at_boundary(api, transport)
    try:
        memory = Memory(api.get("memory/0/0x2000000"), ldt)
        cell, stride = memory.u16(world + 0x738), memory.u16(world + 0x742)
        if not (0 <= x < stride * cell and 0 <= z < memory.u16(world + 4) * cell):
            raise ValueError("Test player position is outside the level")
        old_tile = memory.u32(player + 0x2e) // cell * stride + memory.u32(player + 0x26) // cell
        tile = z // cell * stride + x // cell
        glyph = memory.read(player + 0x5c, 1)
        walls = ds + 0x608a
        floor = ds + 0x718e
        edits = [(player + 0x26, memory.read(player + 0x26, 4), struct.pack("<i", x)),
                 (player + 0x2e, memory.read(player + 0x2e, 4), struct.pack("<i", z))]
        if heading is not None:
            edits.append((player + 0x32, memory.read(player + 0x32, 2), struct.pack("<h", heading)))
        if old_tile != tile:
            old_glyph = memory.read(walls + old_tile, 1)
            if old_glyph == glyph:
                edits.append((walls + old_tile, old_glyph, b" "))
            target = memory.pointer(world + 0x30e + memory.read(walls + tile, 1)[0] * 4, 0x54)
            if target is None or not memory.u16(target + 0x15) & 0x10:
                raise ValueError("Test repositioning targets an impassable tile")
            edits.append((walls + tile, memory.read(walls + tile, 1), glyph))
            occupancy = {}
            for index, delta in ((old_tile, -1), (tile, 1)):
                obj = memory.pointer(world + 0x30e + memory.read(floor + index, 1)[0] * 4, 0x54)
                if obj is not None and memory.u16(obj + 0x12) == 7:
                    occupancy[obj] = occupancy.get(obj, 0) + delta
            for at, delta in occupancy.items():
                count = memory.u16(at + 0x56)
                if not 0 <= count + delta <= 0x7fff:
                    raise ValueError("Test repositioning changes invalid door occupancy")
                edits.append((at + 0x56, memory.read(at + 0x56, 2), struct.pack("<h", count + delta)))
        for at, expected, replacement in edits:
            if api.get(f"memory/{at}/{len(expected)}") != expected:
                raise ValueError("Player position changed while the world was stopped")
        for at, expected, replacement in edits:
            api.put(str(at), replacement, expected=expected)
    finally:
        if owns_gate:
            api.put(str(transport + 46), b"\x01\0")
            wait_for(lambda: api.get(f"memory/{transport + 44}/2") == bytes(2))


def apply_component_plan(api, plan, *, workers=1):
    ordered = sorted(plan, key=lambda change: change.address)
    for left, right in zip(ordered, ordered[1:]):
        if left.address + len(left.replacement) > right.address:
            raise ValueError("Component restoration writes overlap")
    # Merge only adjacent declared edits, never undiscovered native bytes.
    combined = []
    for write in ordered:
        if write.expected == write.replacement:
            continue
        if combined and combined[-1].address + len(combined[-1].replacement) == write.address:
            previous = combined.pop()
            write = events_adapter.NativeWrite(previous.address, previous.expected + write.expected,
                                               previous.replacement + write.replacement)
        combined.append(write)
    # One atomic memory view checks the entire plan before any write. Independent
    # guarded writes can then share an HTTP dispatch tick instead of paying one
    # DOSBox service tick for every scalar. No ranges or gaps are added.
    view = api.get("memory/0/0x2000000") if workers > 1 and combined else None
    for write in combined:
        actual = (view[write.address:write.address + len(write.expected)] if view is not None else
                  api.get(f"memory/{write.address}/{len(write.expected)}"))
        if actual != write.expected:
            raise ValueError("Native state changed before restoration")

    def commit(write):
        api.put(str(write.address), write.replacement, expected=write.expected)

    if workers > 1 and len(combined) > 1:
        # Exiting the pool waits for every outstanding write, including on error;
        # callers must not release the native safe gate while requests are live.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(commit, combined))
    else:
        for write in combined:
            commit(write)


def observe(api, transport, language, level, exercise=False, *, components=None,
            settle_seconds=0, step_door=False, verify_state=None, dismiss_intro=True,
            runtime_layout=None, gate_held=False, keep_gate=False, manual_start=False):
    shift = 0 if language == "ru" else 0xb0
    if runtime_layout is None:
        initial = api.get("memory/0/0x2000000")
        anchor = b"OVERFLOW\0UNDERFLOW\0TLOSS\0PLOSS"
        if initial.count(anchor) != 1:
            raise ValueError("Native data anchor is not unique")
        native_base = initial.find(anchor) - (0x1082 + shift)
        ldt = locate_ldt(initial, native_base, transport)
    else:
        if dismiss_intro or exercise:
            raise ValueError("Cached runtime layout is only valid for an already bound live world")
        native_base, ldt = runtime_layout

    def key(scan):
        keys = native_base + 0x2155 + shift
        pending = struct.unpack("<H", api.get(f"memory/{keys - 3}/2"))[0]
        api.put(str(keys + scan), b"\x03")
        api.put(str(keys - 3), struct.pack("<HB", (pending + 1) & 0xffff, scan))
        time.sleep(.12)
        api.put(str(keys + scan), b"\x02")

    if manual_start:
        # Interactive launch never navigates menus or dismisses an intro.
        # Request 17 was armed before execution and stops after the native fade
        # and the user's confirmation. API disconnection must propagate.
        while api.get(f"memory/{transport + 44}/2") != b"\x01\0":
            time.sleep(.05)
    for _ in range(20):
        world_pointer = api.get(f"memory/{native_base + 0x266a + shift}/4")
        if world_pointer != bytes(4):
            break
        if manual_start:
            raise ValueError("Startup gate reached without a live world")
        if not dismiss_intro:
            raise ValueError("No live world is available")
        time.sleep(.5)
        key(0x1c)
    else:
        raise TimeoutError("Native world was not created")
    if dismiss_intro and not keep_gate and not manual_start:
        time.sleep(1)
    door_exercised = False
    # The world pointer is published before the level's blocking introduction
    # finishes. Wait for the actual frame gate, dismissing that introduction
    # only while the gate has not been reached. A non-null world is insufficient.
    if dismiss_intro and not manual_start:
        if api.get(f"memory/{transport + 44}/2") != b"\x01\0":
            if api.get(f"memory/{transport + 16}/2") != struct.pack("<H", 17):
                api.put(str(transport + 46), bytes(2))
                api.put(str(transport + 16), struct.pack("<H", 13))
        for _ in range(5):
            try:
                wait_for(lambda: api.get(f"memory/{transport + 44}/2") == b"\x01\0", timeout=1)
                break
            except TimeoutError:
                key(0x1c)
        else:
            api.put(str(transport + 16), bytes(2))
            raise TimeoutError("World was created but did not enter the native main loop")
    else:
        gate_held = gate_held or manual_start
        if gate_held:
            if api.get(f"memory/{transport + 44}/2") not in (b"\x01\0", b"\x02\0"):
                raise ValueError("Expected an already held native gate")
        else:
            pause_at_boundary(api, transport)
    already_paused = True
    door_attempts = []
    if exercise:
        api.put(str(transport + 46), b"\x01\0")
        wait_for(lambda: api.get(f"memory/{transport + 44}/2") == bytes(2), poll_interval=.005)
        already_paused = False
        # Native pickups and animation scheduling run normally on disposable
        # copies. Only the player's guest coordinates are moved to real tiles.
        for glyph in (87, 86):
            current = Memory(api.get("memory/0/0x2000000"), ldt)
            world = current.pointer(native_base + 0x266a + shift, 0x74a)
            player = current.pointer(world + 0x442, 0xde)
            cell, stride = current.u16(world + 0x738), current.u16(world + 0x742)
            layer = current.data[native_base + 0x718e + shift:native_base + 0x718e + shift + 4096]
            index = layer.find(bytes((glyph,)))
            if index < 0:
                continue
            reposition_player(api, transport, ldt, native_base + shift, world, player,
                              index % stride * cell + cell // 2, index // stride * cell + cell // 2)
            key(0x11)
            time.sleep(.7)
        for scan in (2, 3, 2, 3):
            key(scan)
            time.sleep(.15)
        current = Memory(api.get("memory/0/0x2000000"), ldt)
        world = current.pointer(native_base + 0x266a + shift, 0x74a)
        player = current.pointer(world + 0x442, 0xde)
        cell, stride = current.u16(world + 0x738), current.u16(world + 0x742)
        floor = current.read(native_base + 0x718e + shift, 4096)
        walls = current.read(native_base + 0x608a + shift, 4096)
        for index, glyph in enumerate(floor):
            door = current.pointer(world + 0x30e + glyph * 4, 0x54)
            if door is None or current.u16(door + 0x12) != 7 or current.u16(door + 0x5c) != 0:
                continue
            adjacent = None
            for delta in (-1, 1, -stride, stride):
                near = index + delta
                if not 0 <= near < len(walls) or abs(near % stride - index % stride) > 1:
                    continue
                wall = current.pointer(world + 0x30e + walls[near] * 4, 0x54)
                if wall is not None and current.u16(wall + 0x15) & 0x10:
                    adjacent = near
                    break
            if adjacent is None:
                continue
            reposition_player(api, transport, ldt, native_base + shift, world, player,
                              adjacent % stride * cell + cell // 2, adjacent // stride * cell + cell // 2)
            for heading in (0, 90, 180, 270):
                if step_door and not already_paused:
                    pause_at_boundary(api, transport)
                    already_paused = True
                api.put(str(player + 0x32), struct.pack("<H", heading))
                if step_door:
                    keys = native_base + 0x2155 + shift
                    pending = struct.unpack("<H", api.get(f"memory/{keys - 3}/2"))[0]
                    api.put(str(keys + 0x39), b"\x03")
                    api.put(str(keys - 3), struct.pack("<HB", (pending + 1) & 0xffff, 0x39))
                    step_at_boundary(api, transport)
                    api.put(str(keys + 0x39), b"\x02")
                    step_at_boundary(api, transport)
                    door_attempts.append((glyph, heading, api.get(f"memory/{door}/2").hex(),
                                          api.get(f"memory/{door + 0x50}/8").hex()))
                else:
                    key(0x39)
                moving = api.get(f"memory/{door + 0x55}/1") == bytes(1)
                if api.get(f"memory/{door}/2") != bytes(2) and (not step_door or moving):
                    door_exercised = True
                    break
            if door_exercised:
                break
        if step_door and not door_exercised:
            raise ValueError(f"Single-step door exercise failed: {door_attempts[-8:]}")
    if settle_seconds:
        if already_paused:
            raise ValueError("Cannot settle a paused world")
        time.sleep(settle_seconds)
    # The request remains pending until the main loop's exact return address
    # AND code selector match. No time-based assumption establishes quiescence.
    if not already_paused:
        pause_at_boundary(api, transport)
    clock_offset = 0x20c6 + shift
    clock_before = api.get(f"memory/{native_base + clock_offset}/10")
    result = None
    try:
        memory = Memory(api.get("memory/0/0x2000000"), ldt)
        world = memory.pointer(native_base + 0x266a + shift, 0x74a)
        interface = memory.pointer(native_base + 0xa5d6 + shift)
        objects = {}
        ids = {}
        for index in range(256):
            pointer = memory.pointer(world + 0x30e + index * 4, 0x54)
            if pointer is not None:
                if pointer in ids:
                    raise ValueError("Aliased native object-table slots")
                ids[pointer] = index + 1
                objects[pointer] = {"native_index": index}
        player = memory.pointer(world + 0x442, 0xde)
        slots = []
        for index in range(9):
            pointer = memory.pointer(player + 0xaa + index * 4, 0x87)
            slots.append(ids.get(pointer) if pointer else None)
            if pointer is not None and pointer not in ids:
                ids[pointer] = 257 + index
                objects[pointer] = {"weapon_slot": index}
                slots[-1] = ids[pointer]
        total_events = 0
        unresolved = []
        for pointer, result in objects.items():
            count, capacity = memory.u16(pointer), memory.u16(pointer + 2)
            result.update(object_id=ids[pointer], native_type=memory.u16(pointer + 0x12),
                          vtable=f"{memory.u16(pointer + 0x10):04x}",
                          events=[], event_capacity=capacity)
            if capacity != 4 or count >= capacity:
                raise ValueError("Unexpected event capacity/count")
            event_array = memory.pointer(pointer + 4, capacity * 16)
            for i in range(count):
                event = event_array + i * 16
                handler = memory.data[event + 8:event + 12]
                descriptors = []
                for offset in (0x502, 0x50a, 0x512, 0x51a, 0x522, 0x52a,
                               0x532, 0x53a, 0x542, 0x54a, 0x552, 0x55a):
                    descriptor = native_base + offset + (0 if language == "ru" else 16)
                    if memory.data[descriptor:descriptor + 4] == handler:
                        descriptors.append(f"{offset:04x}")
                if not descriptors:
                    unresolved.append({"object_id": ids[pointer], "event_index": i})
                result["events"].append({"descriptors_ru": descriptors,
                                         "interval": memory.u32(event + 4),
                                         "parameter": memory.u32(event + 12)})
            total_events += count
        ring_count = memory.u16(native_base + (0x3e8 if language == "ru" else 0x3f0))
        native_events = memory.u16(native_base + (0x3ea if language == "ru" else 0x3f2))
        cursor = memory.pointer(native_base + (0x3ec if language == "ru" else 0x3f4))
        ring = []
        pointer = cursor
        for _ in range(ring_count):
            if pointer not in ids or ids[pointer] in ring:
                raise ValueError("Event ring contains unknown or repeated objects")
            ring.append(ids[pointer])
            next_pointer = memory.pointer(pointer + 8, 16)
            if memory.pointer(next_pointer + 12) != pointer:
                raise ValueError("Event ring backlinks disagree")
            pointer = next_pointer
        if (ring_count and pointer != cursor) or total_events != native_events:
            raise ValueError("Event ring/count inconsistency")
        scheduled = {ids[p] for p in objects if memory.u16(p) > 0}
        if scheduled != set(ring):
            raise ValueError("Scheduled objects differ from the event ring")
        resource_name = memory.pointer(native_base + (0x1f82 if language == "ru" else 0x2032))
        name = memory.data[resource_name:resource_name + 12].split(b"\0")[0].decode("ascii")
        if str(level) not in name:
            raise ValueError(f"Unexpected world resource: {name}")
        native_objects = {}
        for pointer, item in objects.items():
            index = item.get("native_index")
            binding_address = (world + 0x30e + index * 4 if index is not None
                               else player + 0xaa + item["weapon_slot"] * 4)
            native_objects[ids[pointer]] = events_adapter.NativeObject(
                ids[pointer], item["native_type"], pointer,
                memory.read(binding_address, 4), memory.pointer(pointer + 4, 64), item["event_capacity"])
        base_queue = native_base + (0x3e8 if language == "ru" else 0x3f0)
        queue = events_adapter.NativeQueue(base_queue, base_queue + 2, base_queue + 4,
                                          base_queue + 8, base_queue + 12)
        descriptors = (0x502, 0x50a, 0x512, 0x51a, 0x52a, 0x532, 0x53a, 0x542, 0x55a, 0x54a)
        bindings = {kind: memory.read(native_base + offset + (0 if language == "ru" else 16), 4)
                    for kind, offset in enumerate(descriptors, 1)}
        frozen_time = memory.u32(transport + 52)
        event_clock = native_base + clock_offset
        now = ((frozen_time - memory.u32(event_clock + 2)) & 0xffffffff
               if memory.u16(event_clock) else memory.u32(event_clock + 6))
        logical_events = events_adapter.capture(memory.read, native_objects, queue, bindings, now)
        plan = events_adapter.plan_restore(logical_events, memory.read, native_objects, queue, bindings, now)
        doors = []
        for pointer, item in objects.items():
            if item["native_type"] != 7:
                continue
            image = memory.pointer(pointer + 0x44, 18)
            binding = door_adapter.NativeDoor(pointer, image, memory.read(pointer + 0x44, 4))
            state = door_adapter.capture(memory.read, binding)
            plan += door_adapter.plan_restore(state, memory.read, binding)
            doors.append((binding, state))
        component_result = None
        if components is not None:
            # A research action may replace the self-round-trip with an older
            # logical component snapshot. The observer still owns the safe gate,
            # validates every write, and verifies the installed state below.
            plan, logical_events, doors, component_result = components(
                memory, native_objects, queue, bindings, now, doors)
        apply_component_plan(api, plan)
        restored = Memory(api.get("memory/0/0x2000000"), ldt)
        if events_adapter.capture(restored.read, native_objects, queue, bindings, now) != logical_events:
            raise ValueError("Logical event-ring round trip differs")
        if any(door_adapter.capture(restored.read, binding) != state for binding, state in doors):
            raise ValueError("Logical door round trip differs")
        if verify_state is not None:
            verify_state(restored, native_objects)
        result = {"resource": name, "objects": list(objects.values()), "event_ring": ring,
                "exercise": exercise,
                "door_exercised": door_exercised,
                "native_events": native_events, "unresolved_events": unresolved,
                "type_counts": dict(collections.Counter(o["native_type"] for o in objects.values())),
                "player_id": ids[player], "weapon_slots": slots,
                "dimensions": [memory.u16(world), memory.u16(world + 4)],
                "interface_present": interface is not None,
                "event_adapter_round_trip": True,
                "door_adapter_round_trips": len(doors),
                "moving_doors": sum(not state.ready for _, state in doors),
                "safe_boundary": True}
        if component_result is not None:
            result["components"] = component_result
            if component_result.get("restored"):
                for field in ("objects", "event_ring", "native_events"):
                    result[field + "_before_restore"] = result.pop(field)
                result["event_ring"] = list(dict.fromkeys(event.owner_id for event in logical_events))
                result["native_events"] = len(logical_events)
        return result
    finally:
        if not keep_gate or result is None:
            api.put(str(transport + 46), b"\x01\0")
            wait_for(lambda: api.get(f"memory/{transport + 44}/2") == bytes(2), poll_interval=.005)
            delta = struct.unpack("<I", api.get(f"memory/{transport + 56}/4"))[0]
            if struct.unpack_from("<H", clock_before)[0]:
                origin = struct.unpack_from("<I", clock_before, 2)[0]
                after = struct.unpack("<I", api.get(f"memory/{native_base + clock_offset + 2}/4"))[0]
                if after != (origin + delta) & 0xffffffff:
                    raise ValueError("Native event clock did not exclude observation time")
            if result is not None:
                result["excluded_clock_ticks"] = delta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("language", choices=("ru", "en"))
    parser.add_argument("--level", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--exercise", action="store_true")
    parser.add_argument("--nasm", type=Path, default=ROOT / "build/tools/nasm-2.16.03/nasm.exe")
    args = parser.parse_args()
    trial(args.language, args.nasm, args.level,
          lambda api, transport, language, level: observe(api, transport, language, level, args.exercise))
