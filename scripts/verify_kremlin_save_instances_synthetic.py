"""Execute native-instance gate code with invented memory and native ABI stubs.

The actual game constructors are verified separately in DOSBox. These tests
exercise our ASM guards, stack/argument handling and clobber resistance only.
No game bytes, original files or resources are read.
"""
from pathlib import Path
import struct
import subprocess

from verify_kremlin_save_io_synthetic import Machine, ROOT


class InstanceMachine(Machine):
    def __init__(self, payload, operation, template=1, target=5):
        super().__init__(payload, "ru")
        self.operation, self.template, self.target = operation, template, target
        self.started = False
        self.native_calls = []
        self.u.mem_write(0x3266a, struct.pack("<HH", 0, 0x7000))
        self.u.mem_write(0x70312, struct.pack("<HH", 0, 0x7100))
        self.u.mem_write(0x71010, struct.pack("<HH", 0x655, 8))

    def observe(self, u, address, size, data):
        super().observe(u, address, size, data)
        if self.read16(0x2002c) == 1:
            if not self.started:
                self.started = True
                self.u.mem_write(0x20080, struct.pack("<HH", self.template, self.target))
                self.write16(0x20074, self.operation)
            # Keep the native frame stopped until our command is completed.
            self.write16(0x2002e, int(self.read16(0x20076) != 0))
        if address == 0x50500:
            sp = self.reg("SP")
            ip, cs, offset, selector, message, message_selector = struct.unpack("<6H", u.mem_read(0x30000 + sp, 12))
            assert (offset, selector, message, message_selector) == (0, 0x7300, 256, 0x2000)
            self.native_calls.append((address, offset, selector, message))
            self.set_reg("SP", sp + 4)
            self.set_reg("CS", cs)
            self.set_reg("IP", ip)
        if address == 0x50900:
            sp = self.reg("SP")
            ip, cs, offset, selector, waiting = struct.unpack("<5H", u.mem_read(0x30000 + sp, 10))
            assert (offset, selector, waiting) == (1536, 0x2000, 1)
            self.native_calls.append((address, offset, selector, waiting))
            self.set_reg("SP", sp + 4)
            self.set_reg("CS", cs)
            self.set_reg("IP", ip)
        if address == 0x50a00:
            sp = self.reg('SP')
            ip, cs, offset, selector = struct.unpack('<4H', u.mem_read(0x30000 + sp, 8))
            assert (offset, selector) == (0, 0x7300)
            self.native_calls.append((address, offset, selector))
            self.set_reg('SP', sp + 4)
            self.set_reg('CS', cs)
            self.set_reg('IP', ip)
        if address in (0x50300, 0x50400):
            sp = self.reg("SP")
            ip, cs, offset, selector, index = struct.unpack("<5H", u.mem_read(0x30000 + sp, 10))
            self.native_calls.append((address, offset, selector, index))
            if address == 0x50300:
                assert (offset, selector, index) == (0, 0x7100, self.target)
                self.set_reg("AX", 0)
                self.set_reg("DX", 0x7200)
            else:
                assert (offset, selector, index) == (0, 0x7000, self.target)
                u.mem_write(0x7030e + 4 * index, bytes(4))
            for name in ("EBX", "ECX", "ESI", "EDI"):
                self.set_reg(name, 0xa5a51234)
            self.set_reg("ES", 0x7400)
            self.set_reg("FS", 0x7500)
            self.set_reg("GS", 0x7600)
            self.set_reg("SP", sp + 4)
            self.set_reg("CS", cs)
            self.set_reg("IP", ip)

    def run(self):
        self.call(13)
        return self.read16(0x20078)


class EarlyMachine(Machine):
    def __init__(self, payload, *, enabled=1, loading=False, return_ip=0x0c70):
        super().__init__(payload, 'ru')
        self.entry = struct.unpack_from('<H', payload, payload.index(b'KRSEARL1') + 8)[0]
        self.loading, self.return_ip = loading, return_ip
        self.prompted = self.native_resumed = self.returned = False
        self.write16(0x2009c, enabled)

    def observe(self, u, address, size, data):
        super().observe(u, address, size, data)
        if address == 0x50b00:
            self.native_resumed = True
            u.emu_stop()
        if address == 0x40c70:
            self.returned = True
            u.emu_stop()
        if self.read16(0x2002c) == 3:
            assert self.read16(0x3266a) == 0  # no world can exist yet
            self.prompted = True
            self.write16(0x2009e, int(self.loading))
            self.write16(0x2002e, 1)

    def run(self):
        for name, value in {'CS': 0x1000, 'DS': 0x3100, 'SS': 0x3000,
                            'ES': 0x4100, 'FS': 0x4200, 'GS': 0x4300,
                            'EBP': 0xd000, 'ESP': 0xe000, 'EFLAGS': 0x603}.items():
            self.set_reg(name, value)
        self.u.mem_write(0x3e000, struct.pack('<2H', self.return_ip, 0x4000))
        self.u.emu_start(0x10000 + self.entry, 0x100000, count=100000)
        assert self.reg('DS') == 0x3100 and self.reg('ES') == 0x4100
        assert self.reg('FS') == 0x4200 and self.reg('GS') == 0x4300
        return self


class DeathMachine(Machine):
    def __init__(self, payload, *, enabled=1, health=0, return_ip=0x1005, replacement=False):
        super().__init__(payload, "ru")
        self.death_entry = struct.unpack_from("<H", payload, payload.index(b"KRSLIVE1") + 8)[0]
        self.return_ip, self.replacement = return_ip, replacement
        self.native_resumed = self.prompted = self.returned = False
        self.write16(0x20090, enabled)
        self.write16(0x7105e, health)
        self.u.mem_write(0x20600, b"Choice\0")

    def observe(self, u, address, size, data):
        super().observe(u, address, size, data)
        if address == 0x50800:
            self.native_resumed = True
            u.emu_stop()
        if address == 0x41005:
            self.returned = True
            u.emu_stop()
        if self.read16(0x20092) == 1:
            if not self.prompted:
                assert self.read16(0x2002e) == 0  # stale release was consumed
            self.prompted = True
            self.write16(0x20094, int(self.replacement))
            self.write16(0x2002e, 1)

    def run(self):
        for name, value in {"CS": 0x1000, "DS": 0x3100, "SS": 0x3000, "ES": 0x4100,
                            "FS": 0x4200, "GS": 0x4300, "EAX": 0x11112222,
                            "EBX": 0x33334444, "ECX": 0x55556666, "EDX": 0x77778888,
                            "ESI": 0x12345678, "EDI": 0x23456789, "EBP": 0xd000,
                            "ESP": 0xe000, "EFLAGS": 0x603}.items():
            self.set_reg(name, value)
        self.write16(0x2002e, 1)
        self.u.mem_write(0x3e000, struct.pack("<4H", self.return_ip, 0x4000, 0, 0x7100))
        self.u.emu_start(0x10000 + self.death_entry, 0x100000, count=100000)
        for name, value in (("DS", 0x3100), ("ES", 0x4100), ("FS", 0x4200), ("GS", 0x4300),
                            ("EBX", 0x33334444), ("ECX", 0x55556666), ("EDX", 0x77778888)):
            assert self.reg(name) == value
        if self.native_resumed:
            assert self.reg("AX") == 0x3000 and self.reg("BP") == 0xd001
            assert self.reg("SP") == 0xdffe
        else:
            assert self.returned and self.reg("AX") == 0 and self.reg("BP") == 0xd000
            assert self.reg("SP") == 0xe004
        return self


def checks(payload):
    machine = InstanceMachine(payload, 1)
    assert machine.run() == 0
    assert bytes(machine.u.mem_read(0x70322, 4)) == struct.pack("<HH", 0, 0x7200)
    assert machine.read16(0x7073c) == 1
    assert len(machine.native_calls) == 1
    assert machine.read16(0x2002c) == 0
    for operation, template, target in ((99, 1, 5), (1, 256, 5), (1, 1, 256), (1, 2, 5), (2, 0, 5), (4, 0, 0)):
        invalid = InstanceMachine(payload, operation, template, target)
        assert invalid.run() == 1
        assert not invalid.native_calls and invalid.read16(0x7073c) == 0
    occupied = InstanceMachine(payload, 1, 1, 1)
    assert occupied.run() == 2 and not occupied.native_calls
    wrong_type = InstanceMachine(payload, 1)
    wrong_type.write16(0x71012, 1)
    assert wrong_type.run() == 1 and not wrong_type.native_calls
    wrong_vtable = InstanceMachine(payload, 1)
    wrong_vtable.write16(0x71010, 0x66d)
    assert wrong_vtable.run() == 1 and not wrong_vtable.native_calls
    remove = InstanceMachine(payload, 2, 0, 1)
    assert remove.run() == 0 and len(remove.native_calls) == 1
    assert bytes(remove.u.mem_read(0x70312, 4)) == bytes(4)
    message = InstanceMachine(payload, 3)
    message.u.mem_write(0x3a5d6, struct.pack("<HH", 0, 0x7300))
    message.u.mem_write(0x20100, b"Synthetic saved\0")
    assert message.run() == 0 and len(message.native_calls) == 1
    unterminated = InstanceMachine(payload, 3)
    unterminated.u.mem_write(0x3a5d6, struct.pack("<HH", 0, 0x7300))
    unterminated.u.mem_write(0x20100, b"X" * 39)
    assert unterminated.run() == 1 and not unterminated.native_calls
    missing_interface = InstanceMachine(payload, 3)
    assert missing_interface.run() == 1 and not missing_interface.native_calls
    for content, expected in ((b'Choice\0', 0), (b'X' * 256, 1)):
        dialog = InstanceMachine(payload, 5)
        dialog.u.mem_write(0x3a5d6, struct.pack('<HH', 0, 0x7300))
        dialog.u.mem_write(0x20600, content)
        assert dialog.run() == expected
        assert len(dialog.native_calls) == int(expected == 0)
    no_interface = InstanceMachine(payload, 5)
    assert no_interface.run() == 1 and not no_interface.native_calls
    for arguments in ({"enabled": 0}, {"health": 1}, {"health": 0xffff}, {"return_ip": 0xcc7}):
        death = DeathMachine(payload, **arguments).run()
        assert death.native_resumed and not death.prompted
    no_longer_available = DeathMachine(payload).run()
    assert no_longer_available.native_resumed and no_longer_available.prompted
    replaced = DeathMachine(payload, replacement=True).run()
    assert replaced.returned and replaced.prompted and not replaced.native_resumed
    assert replaced.read16(0x2002c) == 0 and replaced.read16(0x20092) == 0
    # Startup must pass through all native introduction/fade frames. The
    # intro-complete marker is a trusted main-frame local, never a host timer.
    for frame in range(7):
        startup = Machine(payload, 'ru')
        startup.write16(0x3d000 - 0x3a, frame)
        startup.call(17)
        assert startup.held == (frame == 6)
        assert startup.read16(0x20010) == (0 if frame == 6 else 17)
    for arguments in ({'enabled': 0}, {'return_ip': 0xc71}):
        early = EarlyMachine(payload, **arguments).run()
        assert early.native_resumed and not early.prompted
    for loading in (False, True):
        early = EarlyMachine(payload, loading=loading).run()
        assert early.prompted and early.native_resumed != loading and early.returned == loading
        assert early.read16(0x2009c) == 0 and early.read16(0x2002c) == 0
        assert early.reg('SP') == (0xe004 if loading else 0xdffe)
    dialog = InstanceMachine(payload, 5)
    dialog.u.mem_write(0x3266a, bytes(4))
    dialog.u.mem_write(0x3a5d6, struct.pack('<HH', 0, 0x7300))
    dialog.u.mem_write(0x20600, b'Before world\0')
    assert dialog.run() == 0 and len(dialog.native_calls) == 1
    palette = InstanceMachine(payload, 6)
    palette.u.mem_write(0x3b48c, struct.pack('<HH', 0, 0x7300))
    assert palette.run() == 0 and len(palette.native_calls) == 1
    intro = InstanceMachine(payload, 7)
    assert intro.run() == 0 and intro.read16(0x3d000 - 0x3a) == 1
    return 37


if __name__ == "__main__":
    output = ROOT / "build/save-research/world-ru.bin"
    subprocess.run([str(ROOT / "build/tools/nasm-2.16.03/nasm.exe"), "-f", "bin", "-I",
                    str(ROOT / "src/wasdify/patches") + "/",
                    str(ROOT / "src/wasdify/patches/kremlin_save_world.asm"), "-o", str(output)], check=True)
    print(checks(output.read_bytes()), "native-instance gate checks passed")
