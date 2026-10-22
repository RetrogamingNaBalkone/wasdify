from dataclasses import replace
import struct
import unittest

from wasdify import kremlin_save_doors as doors
from wasdify.kremlin_save import SaveFormatError


class DoorAdapterTests(unittest.TestCase):
    def native(self, offset):
        memory = bytearray(2048)
        binding = doors.NativeDoor(offset, offset + 0x6c, struct.pack("<HH", offset + 0x6c, 0x1234))
        struct.pack_into("<H", memory, offset + 0x12, 7)
        memory[offset + 0x44:offset + 0x48] = binding.image_pointer
        struct.pack_into("<BBh", memory, offset + 0x54, 2, 1, 0)
        struct.pack_into("<h", memory, offset + 0x60, 1)
        memory[offset + 0x7a:offset + 0x7e] = b"new!"  # fresh resource pixel pointer
        return memory, binding

    def test_opening_and_closing_round_trip_without_old_resource_pointers(self):
        for direction in (1, 2):
            memory, binding = self.native(512)
            read = lambda a, size: bytes(memory[a:a + size])
            state = doors.DoorState(0x1234, 0x7f, 4, direction, 0, 2, 8)
            before = bytes(memory)
            plan = doors.plan_restore(state, read, binding)
            self.assertEqual(bytes(memory), before)
            for change in plan:
                memory[change.address:change.address + len(change.replacement)] = change.replacement
            self.assertEqual(doors.capture(read, binding), state)
            self.assertEqual(read(binding.image_address + 6, 4), struct.pack("<hh", 8, 4))
            self.assertEqual(read(binding.image_address + 14, 4), b"new!")
            self.assertEqual(read(binding.address + 0x44, 4), binding.image_pointer)

    def test_invalid_fields_and_native_bindings_reject_before_writing(self):
        memory, binding = self.native(256)
        read = lambda a, size: bytes(memory[a:a + size])
        state = doors.DoorState(0, 0, 0, 2, 1, 0, 1)
        for change in ({"position": -1}, {"position": 2}, {"direction": 3},
                       {"ready": 2}, {"occupants": -1}, {"steps": 32768}, {"flags": 65536}):
            with self.subTest(change=change), self.assertRaises(SaveFormatError):
                doors.plan_restore(replace(state, **change), read, binding)
        with self.assertRaises(SaveFormatError):
            doors.plan_restore(state, read, replace(binding, image_address=0))
        struct.pack_into("<H", memory, binding.address + 0x12, 9)
        with self.assertRaises(SaveFormatError):
            doors.plan_restore(state, read, binding)
