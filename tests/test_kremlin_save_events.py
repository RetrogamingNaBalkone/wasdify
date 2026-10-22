"""Relocation and corruption tests use only invented native memory and IDs."""
from dataclasses import replace
import struct
import unittest

from wasdify import kremlin_save_events as adapter
from wasdify.kremlin_save import EventRecord, SaveFormatError


class NativeMemory:
    def __init__(self, relocation=0):
        self.data = bytearray(16384)
        self.callbacks = {kind: struct.pack("<HH", kind * 32, 0x1234 + relocation)
                          for kind in adapter.EVENT_OWNERS}
        self.objects = {
            7: adapter.NativeObject(7, 1, 0x100 + relocation, struct.pack("<I", 100 + relocation),
                                    0x1000 + relocation, 4),
            50: adapter.NativeObject(50, 8, 0x200 + relocation, struct.pack("<I", 200 + relocation),
                                     0x1100 + relocation, 4),
        }
        self.queue = adapter.NativeQueue(20, 22, 24, 28, 32)
        for obj in self.objects.values():
            struct.pack_into("<HH", self.data, obj.address, 0, 4)

    def read(self, address, size):
        return bytes(self.data[address:address + size])

    def events(self):
        return (EventRecord(1, 50, adapter.PROJECTILE, 1, {1: 150, 2: 10, 3: 0, 4: 0, 5: 0}),
                EventRecord(2, 7, adapter.PLAYER, 1, {1: 0, 2: 20, 3: 1, 4: 0, 5: 0}),
                EventRecord(3, 7, adapter.SWITCH_WEAPON, 1, {1: 50, 2: 30, 3: 1, 4: 1, 5: 0}))

    def plan(self, events=None, now=150):
        return adapter.plan_restore(self.events() if events is None else events, self.read,
                                    self.objects, self.queue, self.callbacks, now)

    def apply(self, writes):
        # Test-only memory writer. The game installer is still unimplemented.
        for write in writes:
            assert self.read(write.address, len(write.expected)) == write.expected
        for write in writes:
            self.data[write.address:write.address + len(write.replacement)] = write.replacement

    def capture(self, now=150):
        return adapter.capture(self.read, self.objects, self.queue, self.callbacks, now)


class EventAdapterTests(unittest.TestCase):
    def test_round_trip_across_different_allocations_and_code_selectors(self):
        old = NativeMemory()
        old.apply(old.plan())
        snapshot = old.capture()
        self.assertEqual(snapshot, old.events())
        new = NativeMemory(0x400)
        before = bytes(new.data)
        plan = new.plan(snapshot, now=10000)
        self.assertEqual(bytes(new.data), before)
        new.apply(plan)
        self.assertEqual(new.capture(now=10000), snapshot)
        self.assertEqual(new.read(24, 4), new.objects[50].pointer)
        for obj in new.objects.values():
            next_pointer = new.read(obj.address + 8, 4)
            previous_pointer = new.read(obj.address + 12, 4)
            other = next(o for o in new.objects.values() if o != obj)
            self.assertEqual(next_pointer, other.pointer)
            self.assertEqual(previous_pointer, other.pointer)
        self.assertEqual(new.read(new.objects[50].events_address + 8, 4),
                         new.callbacks[adapter.PROJECTILE])

    def test_overdue_event_becomes_zero_remaining_time(self):
        memory = NativeMemory()
        memory.apply(memory.plan())
        struct.pack_into("<I", memory.data, memory.objects[7].events_address, 100)
        self.assertEqual(memory.capture()[1].fields[1], 0)

    def test_invalid_event_records_never_produce_a_write_plan(self):
        memory = NativeMemory()
        events = memory.events()
        changes = [replace(events[0], event_id=0), replace(events[0], owner_id=99),
                   replace(events[0], event_type=99), replace(events[0], event_version=2),
                   replace(events[0], event_type=adapter.AI),
                   replace(events[0], fields={**events[0].fields, 5: 1}),
                   replace(events[0], fields={**events[0].fields, 3: 1}),
                   replace(events[0], fields={**events[0].fields, 4: 1}),
                   replace(events[0], fields={**events[0].fields, 1: -1}),
                   replace(events[0], fields={**events[0].fields, 2: 1 << 32}),
                   replace(events[0], fields={**events[0].fields, 6: 0})]
        before = bytes(memory.data)
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(SaveFormatError):
                memory.plan((changed,) + events[1:])
        with self.assertRaises(SaveFormatError):
            memory.plan(events + (replace(events[0], event_id=4),))
        with self.assertRaises(SaveFormatError):
            memory.plan(events + (events[0],))
        with self.assertRaisesRegex(SaveFormatError, "event_time_overflow"):
            memory.plan(now=0xffffffff)
        self.assertEqual(bytes(memory.data), before)

    def test_native_ring_corruption_and_unknown_callback_are_rejected(self):
        for address, replacement in ((20, struct.pack("<H", 1)),
                                     (22, struct.pack("<H", 4)), (24, b"xxxx"),
                                     (0x100 + 12, b"xxxx"), (0x1000 + 8, b"xxxx"),
                                     (0x1000 + 12, struct.pack("<I", 1)),
                                     (0x100, struct.pack("<H", 4))):
            memory = NativeMemory()
            memory.apply(memory.plan())
            memory.data[address:address + len(replacement)] = replacement
            with self.subTest(address=address), self.assertRaises(SaveFormatError):
                memory.capture()

    def test_bindings_cannot_alias_objects_arrays_or_callbacks(self):
        memory = NativeMemory()
        memory.objects[50] = replace(memory.objects[50], events_address=memory.objects[7].events_address)
        with self.assertRaisesRegex(SaveFormatError, "native_overlap"):
            memory.plan()
        memory = NativeMemory()
        memory.callbacks[adapter.AI] = memory.callbacks[adapter.PLAYER]
        with self.assertRaisesRegex(SaveFormatError, "event_binding"):
            memory.plan()
        memory = NativeMemory()
        memory.objects[50] = replace(memory.objects[50], pointer=memory.objects[7].pointer)
        with self.assertRaisesRegex(SaveFormatError, "object_binding"):
            memory.plan()

    def test_empty_ring_and_capacity_limit(self):
        memory = NativeMemory()
        memory.apply(memory.plan(()))
        self.assertEqual(memory.capture(), ())
        self.assertEqual(memory.read(24, 4), bytes(4))
        # One unused array slot is required by the native insertion routine.
        event = memory.events()[1]
        four = tuple(replace(event, event_id=i + 1, fields={**event.fields, 3: 0, 4: i}) for i in range(4))
        with self.assertRaisesRegex(SaveFormatError, "event_capacity"):
            memory.plan(four)

    def test_deferred_door_close_is_a_distinct_callback(self):
        memory = NativeMemory()
        memory.objects[50] = replace(memory.objects[50], native_type=7)
        event = replace(memory.events()[0], event_type=adapter.AUTO_CLOSE)
        memory.apply(memory.plan((event,)))
        self.assertEqual(memory.capture(), (event,))
        self.assertNotEqual(memory.callbacks[adapter.AUTO_CLOSE], memory.callbacks[adapter.CLOSE_DOOR])


if __name__ == "__main__":
    unittest.main()
