"""Synthetic guest memory only; no game files or dumps are test fixtures."""
import json
import struct
import unittest

from scripts.profile_kremlin import DATA_ANCHOR, FUNCTIONS, MAGIC, counters, runtime


def descriptor(base):
    return struct.pack("<HHBBBB", 65535, base & 65535, (base >> 16) & 255,
                       0xf2, 0, (base >> 24) & 255)


class Guest:
    def __init__(self):
        self.memory = bytearray(0x100000)
        self.ss, self.ldt, self.profile, self.buffer = 0x2000, 0x30000, 0x40000, 0x50000
        self.memory[self.ss:self.ss+128] = bytes(range(128))
        self.memory[self.ss+0x1082:self.ss+0x1082+len(DATA_ANCHOR)] = DATA_ANCHOR
        system = bytearray(descriptor(self.ldt))
        system[5] = 0x82
        self.memory[0x200:0x208] = system
        self.memory[self.ldt+0x4d8:self.ldt+0x4e0] = descriptor(self.ss)
        self.memory[self.ldt+0x658:self.ldt+0x660] = descriptor(self.buffer)
        self.memory[self.ldt+0x3f0:self.ldt+0x3f8] = descriptor(self.profile)
        self.memory[self.profile+16:self.profile+32] = MAGIC
        struct.pack_into("<HH", self.memory, self.ss+0xb53a, 7, 0x65f)

    def get(self, path):
        if path == "cpu/state":
            return json.dumps({"registers": {"ss": 0x4df}}).encode()
        _, address, *rest = path.split("/")
        if address == "SS":
            offset, size = map(lambda v: int(v, 0), rest)
            return bytes(self.memory[self.ss+offset:self.ss+offset+size])
        address, size = int(address, 0), int(rest[0], 0)
        return bytes(self.memory[address:address+size])


class ProfilerTests(unittest.TestCase):
    def test_protected_buffer_uses_ldt_not_real_mode_selector(self):
        guest = Guest()
        result = runtime(guest)
        self.assertEqual(result["ldt_base"], guest.ldt)
        self.assertEqual(result["buffer_base"], guest.buffer+7)
        self.assertEqual(result["counter_base"], guest.profile+32)

    def test_ambiguous_profile_is_rejected(self):
        guest = Guest()
        guest.memory[0x60000:0x60010] = MAGIC
        with self.assertRaises(ValueError):
            runtime(guest)

    def test_missing_descriptor_is_rejected(self):
        guest = Guest()
        guest.memory[guest.ldt+0x3f0:guest.ldt+0x3f8] = bytes(8)
        with self.assertRaises(ValueError):
            runtime(guest)

    def test_counter_high_word_and_completion_are_distinct(self):
        guest = Guest()
        address = guest.profile+32
        struct.pack_into("<6I", guest.memory, address, 10, 123, 7, 3, 9, 0)
        result = counters(guest, address)
        self.assertEqual(result["horizontal"], {"calls": 10, "ticks": (3 << 32)+7,
                                                "completed": 9, "last_start": 123})
        self.assertEqual(len(result), len(FUNCTIONS))


if __name__ == "__main__":
    unittest.main()
