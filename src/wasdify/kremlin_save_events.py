"""Logical event-ring adapter, shared by native-state research and validation.

No production schema ID is assigned here. Native pointers are trusted bindings
obtained from the paused target, never serialized fields. Planning is read-only;
the caller must keep the world stopped throughout validation and installation.
"""
from dataclasses import dataclass
import struct
from typing import Callable, Mapping

from .kremlin_save import EventRecord, SaveFormatError


# Stable meanings within this unreleased adapter; not original code addresses.
(ANIMATION, HAZARD, PLAYER, SWITCH_WEAPON, PROJECTILE, WEAPON,
 CLOSE_DOOR, OPEN_DOOR, AI, AUTO_CLOSE) = range(1, 11)
EVENT_OWNERS = {
    ANIMATION: frozenset(range(14)) - {11}, HAZARD: frozenset({12}),
    PLAYER: frozenset({1}), SWITCH_WEAPON: frozenset({1}), PROJECTILE: frozenset({8}),
    WEAPON: frozenset({10}), CLOSE_DOOR: frozenset({7}), OPEN_DOOR: frozenset({7}), AI: frozenset({9}),
    AUTO_CLOSE: frozenset({7}),
}


def require(condition, code):
    if not condition:
        raise SaveFormatError(code)


@dataclass(frozen=True)
class NativeObject:
    object_id: int
    native_type: int
    address: int
    pointer: bytes
    events_address: int
    capacity: int


@dataclass(frozen=True)
class NativeQueue:
    object_count_address: int
    event_count_address: int
    cursor_address: int
    scratch_address: int
    callback_flag_address: int


@dataclass(frozen=True)
class NativeWrite:
    address: int
    expected: bytes
    replacement: bytes


def validate_bindings(objects, callbacks):
    require(len(objects) <= 4096, "object_limit")
    require(all(key == o.object_id and type(key) is int and 1 <= key <= 0xffffffff
                and o.native_type in EVENT_OWNERS[ANIMATION] and o.capacity == 4
                and o.address >= 0 and o.events_address >= 0
                and len(o.pointer) == 4 and o.pointer != bytes(4)
                for key, o in objects.items()), "object_binding")
    require(len({o.pointer for o in objects.values()}) == len(objects), "object_binding")
    require(set(callbacks) == set(EVENT_OWNERS)
            and len(set(callbacks.values())) == len(callbacks)
            and all(len(p) == 4 and p != bytes(4) for p in callbacks.values()), "event_binding")


def validate(events: tuple[EventRecord, ...], objects: Mapping[int, NativeObject]):
    """Check grouping, capacities, ownership and fields before allocating/writing."""
    seen_ids, seen_owners = set(), set()
    current, ordinal, slot = None, -1, 0
    require(len(objects) <= 4096 and len(events) <= 16384, "event_limit")
    for event in events:
        require(type(event.event_id) is int and 1 <= event.event_id <= 0xffffffff
                and event.event_id not in seen_ids, "event_id")
        seen_ids.add(event.event_id)
        require(event.owner_id in objects, "event_owner")
        owner = objects[event.owner_id]
        require(event.event_version == 1 and event.event_type in EVENT_OWNERS, "unknown_record_type")
        require(owner.native_type in EVENT_OWNERS[event.event_type], "event_owner_type")
        require(set(event.fields) == {1, 2, 3, 4, 5}, "event_fields")
        require(all(type(v) is int for v in event.fields.values()), "event_fields")
        remaining, interval, queue_order, slot_order, parameter = (event.fields[i] for i in range(1, 6))
        require(0 <= remaining <= 0xffffffff and 0 <= interval <= 0xffffffff, "event_time")
        require(parameter == 0, "event_parameter")
        if event.owner_id != current:
            require(event.owner_id not in seen_owners, "event_owner_order")
            seen_owners.add(event.owner_id)
            current, ordinal, slot = event.owner_id, ordinal + 1, 0
        require(queue_order == ordinal and slot_order == slot, "event_order")
        require(owner.capacity == 4 and slot < owner.capacity - 1, "event_capacity")
        slot += 1


def capture(read: Callable[[int, int], bytes], objects: Mapping[int, NativeObject],
            queue: NativeQueue, callbacks: Mapping[int, bytes], now: int) -> tuple[EventRecord, ...]:
    """Capture the native ring as IDs, operations, relative times and ordering."""
    require(0 <= now <= 0xffffffff, "event_time")
    validate_bindings(objects, callbacks)
    by_pointer = {obj.pointer: obj for obj in objects.values()}
    callback_ids = {pointer: kind for kind, pointer in callbacks.items()}
    ring_count, = struct.unpack("<H", read(queue.object_count_address, 2))
    total, = struct.unpack("<H", read(queue.event_count_address, 2))
    cursor = read(queue.cursor_address, 4)
    require(ring_count <= len(objects), "event_ring")
    pointer, visited, result = cursor, [], []
    for ordinal in range(ring_count):
        require(pointer in by_pointer and pointer not in visited, "event_ring")
        owner = by_pointer[pointer]
        visited.append(pointer)
        count, capacity = struct.unpack("<HH", read(owner.address, 4))
        require(capacity == owner.capacity and 0 < count < capacity, "event_capacity")
        next_pointer = read(owner.address + 8, 4)
        require(next_pointer in by_pointer
                and read(by_pointer[next_pointer].address + 12, 4) == pointer, "event_ring")
        for slot in range(count):
            deadline, interval, callback, parameter = struct.unpack(
                "<II4sI", read(owner.events_address + slot * 16, 16))
            require(callback in callback_ids, "unknown_record_type")
            fields = {1: max(0, deadline - now), 2: interval, 3: ordinal, 4: slot, 5: parameter}
            result.append(EventRecord(len(result) + 1, owner.object_id, callback_ids[callback], 1, fields))
        pointer = next_pointer
    require(not ring_count or pointer == cursor, "event_ring")
    require(len(result) == total, "event_count")
    scheduled = {obj.pointer for obj in objects.values() if struct.unpack("<H", read(obj.address, 2))[0]}
    require(scheduled == set(visited), "event_ring")
    events = tuple(result)
    validate(events, objects)
    return events


def plan_restore(events: tuple[EventRecord, ...], read: Callable[[int, int], bytes],
                 objects: Mapping[int, NativeObject], queue: NativeQueue,
                 callbacks: Mapping[int, bytes], now: int) -> tuple[NativeWrite, ...]:
    """Bind to fresh allocations without invoking callbacks or trusting file pointers."""
    validate(events, objects)
    require(0 <= now <= 0xffffffff, "event_time")
    validate_bindings(objects, callbacks)
    grouped = {}
    for event in events:
        require(event.fields[1] <= 0xffffffff - now, "event_time_overflow")
        grouped.setdefault(event.owner_id, []).append(event)
    order = list(grouped)
    writes = []

    def write(address, replacement):
        expected = read(address, len(replacement))
        require(len(expected) == len(replacement), "native_range")
        writes.append(NativeWrite(address, expected, replacement))

    for obj in objects.values():
        _, capacity = struct.unpack("<HH", read(obj.address, 4))
        require(capacity == obj.capacity == 4, "event_capacity")
        owned = grouped.get(obj.object_id, [])
        array = bytearray(obj.capacity * 16)
        for slot, event in enumerate(owned):
            struct.pack_into("<II4sI", array, slot * 16, now + event.fields[1], event.fields[2],
                             callbacks[event.event_type], 0)
        write(obj.events_address, bytes(array))
        write(obj.address, struct.pack("<H", len(owned)))
        if owned:
            index = order.index(obj.object_id)
            links = objects[order[(index + 1) % len(order)]].pointer
            links += objects[order[(index - 1) % len(order)]].pointer
        else:
            links = bytes(8)
        write(obj.address + 8, links)
    write(queue.object_count_address, struct.pack("<H", len(grouped)))
    write(queue.event_count_address, struct.pack("<H", len(events)))
    write(queue.cursor_address, objects[order[0]].pointer if order else bytes(4))
    write(queue.scratch_address, bytes(4))
    write(queue.callback_flag_address, bytes(1))
    # Reject aliases between arrays, object headers and globals before the caller
    # can perform its first write. A malformed binding cannot corrupt another node.
    ordered = sorted(writes, key=lambda w: w.address)
    for left, right in zip(ordered, ordered[1:]):
        require(left.address + len(left.replacement) <= right.address, "native_overlap")
    return tuple(writes)
