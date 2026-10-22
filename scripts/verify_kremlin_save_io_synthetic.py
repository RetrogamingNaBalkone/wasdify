"""Execute the real 16-bit KRS transport with synthetic DPMI/DOS failures.

Development dependencies: NASM and Unicorn (neither is an application runtime
dependency). No game executable or resource is read by these checks.
"""
import argparse
from pathlib import Path
import struct
import subprocess
from zlib import crc32

from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_HOOK_CODE, UC_HOOK_INTR
from unicorn import x86_const as x86


ROOT = Path(__file__).resolve().parents[1]
REGISTERS = ("EAX", "EBX", "ECX", "EDX", "ESI", "EDI", "EBP", "ESP",
             "DS", "ES", "FS", "GS", "SS", "EFLAGS")


class Machine:
    def __init__(self, payload, language):
        self.u = Uc(UC_ARCH_X86, UC_MODE_16)
        self.u.mem_map(0, 0x100000)
        self.language = language
        self.entry, fixups, count = struct.unpack_from("<3H", payload, 8)
        code = bytearray(payload)
        for i in range(count):
            offset, token = struct.unpack_from("<HH", code, fixups + i * 4)
            value = {0: struct.pack("<H", 0x2000), 1: struct.pack("<HH", 0x100, 0x5000),
                     2: struct.pack("<H", 0x4000), 3: struct.pack("<HH", 0x200, 0x5000),
                     4: struct.pack("<HH", 0x300, 0x5000),
                     5: struct.pack("<HH", 0x400, 0x5000),
                     6: struct.pack("<HH", 0x500, 0x5000),
                     7: struct.pack("<HH", 0x600, 0x5000),
                     8: struct.pack("<HH", 0x700, 0x5000),
                     9: struct.pack("<HH", 0x800, 0x5000),
                     10: struct.pack("<HH", 0x900, 0x5000),
                     11: struct.pack("<HH", 0xa00, 0x5000),
                     12: struct.pack("<HH", 0xb00, 0x5000)}[token]
            code[offset:offset + len(value)] = value
        self.u.mem_write(0x10000, bytes(code))
        self.u.hook_add(UC_HOOK_CODE, self.observe)
        self.u.hook_add(UC_HOOK_INTR, self.interrupt)
        self.calls = []
        self.failure = None
        self.files = {}
        self.handles = {}
        self.held = False
        self.gate_release_flags = []
        self.clock = 10000

    def read16(self, address):
        return struct.unpack("<H", self.u.mem_read(address, 2))[0]

    def write16(self, address, value):
        self.u.mem_write(address, struct.pack("<H", value))

    def reg(self, name):
        return self.u.reg_read(getattr(x86, "UC_X86_REG_" + name))

    def set_reg(self, name, value):
        self.u.reg_write(getattr(x86, "UC_X86_REG_" + name), value)

    def carry(self, value):
        self.set_reg("EFLAGS", self.reg("EFLAGS") & ~1 | int(value))

    def observe(self, u, address, size, data):
        if address == 0x50100:
            u.emu_stop()
        if address == 0x50200:
            sp = self.reg("SP")
            ip, cs, offset, selector = struct.unpack("<4H", u.mem_read(0x30000 + sp, 8))
            u.mem_write((selector << 4) + offset, struct.pack("<I", self.clock))
            self.clock += 1234
            self.set_reg("SP", sp + 4)
            self.set_reg("CS", cs)
            self.set_reg("IP", ip)
        if self.read16(0x2002c) == 1:
            if not self.held:
                self.gate_release_flags.append(self.read16(0x2002e))
            self.held = True
            self.write16(0x2002e, 1)

    def interrupt(self, u, number, data):
        assert number == 0x31, number
        service = self.reg("AX")
        self.calls.append(service)
        if self.failure == "dpmi":
            self.set_reg("AX", 0x8011)
            self.carry(True)
            return
        self.carry(False)
        if service == 0x100:
            assert self.reg("BX") == 0x200
            self.set_reg("AX", 0x6000)
            self.set_reg("DX", 0x6000)
            return
        if service == 0x101:
            assert self.reg("DX") == 0x6000
            return
        assert service == 0x300
        assert self.reg("BX") == 0x21 and self.reg("CX") == 0
        assert self.reg("ES") == 0x2000 and self.reg("DI") == 64
        registers = bytearray(u.mem_read(0x20040, 50))
        edi, esi, ebp, reserved, ebx, edx, ecx, eax = struct.unpack_from("<8I", registers)
        flags, es, ds, fs, gs, ip, cs, sp, ss = struct.unpack_from("<9H", registers, 32)
        assert flags == 0x200 and ds == es == 0x6000 and ss == sp == 0
        assert reserved == ebp == fs == gs == ip == cs == 0
        error, result = 0, 0

        def path(offset):
            data = bytes(u.mem_read((ds << 4) + offset, 256))
            assert b"\0" in data
            return data.split(b"\0")[0]

        if self.failure == "dos":
            error = 5
        elif eax >> 8 == 0x5b:
            name = path(edx)
            if name in self.files:
                error = 80
            else:
                self.files[name] = bytearray()
                result = next(i for i in range(6, 255) if i not in self.handles)
                self.handles[result] = [name, 0]
        elif eax >> 8 == 0x3d:
            name = path(edx)
            if name not in self.files:
                error = 2
            else:
                result = next(i for i in range(6, 255) if i not in self.handles)
                self.handles[result] = [name, 0]
        elif eax >> 8 in (0x3e, 0x3f, 0x40, 0x68, 0x5c):
            if ebx not in self.handles:
                error = 6
            elif eax >> 8 == 0x3e:
                del self.handles[ebx]
            elif eax >> 8 in (0x3f, 0x40):
                name, position = self.handles[ebx]
                if eax >> 8 == 0x40:
                    result = ecx // 2 if self.failure == "short_write" else ecx
                    self.files[name][position:position + result] = u.mem_read((ds << 4) + edx, result)
                else:
                    block = bytes(self.files[name][position:position + ecx])
                    result = len(block)
                    u.mem_write((ds << 4) + edx, block)
                self.handles[ebx][1] += result
            elif eax >> 8 == 0x5c:
                assert ecx == edx == esi == 0 and edi == 1
        elif eax >> 8 == 0x56:
            old, new = path(edx), path(edi)
            if old not in self.files:
                error = 2
            elif new in self.files:
                error = 5
            else:
                self.files[new] = self.files.pop(old)
        elif eax >> 8 == 0x41:
            name = path(edx)
            if name not in self.files:
                error = 2
            else:
                del self.files[name]
        else:
            raise AssertionError(hex(eax))
        struct.pack_into("<I", registers, 28, error or result)
        struct.pack_into("<H", registers, 32, flags | int(bool(error)))
        u.mem_write(0x20040, bytes(registers))
        # Adversarial scratch-register changes: the trampoline must restore all
        # caller values even when the extender clobbers 32-bit register halves.
        for name in ("EAX", "EBX", "ECX", "EDX", "ESI", "EDI", "EBP"):
            self.set_reg(name, 0xa5a51234)

    def call(self, operation, *, handle=0, length=0, data=b"", a=b"", b=b"", boundary=True,
             stale_release=False):
        self.write16(0x20010, operation)
        self.u.mem_write(0x20022, struct.pack("<HH", handle, length))
        self.u.mem_write(0x20200, a + b"\0")
        self.u.mem_write(0x20300, b + b"\0")
        if data:
            self.u.mem_write(0x20400, data)
        self.held = False
        self.u.mem_write(0x2002c, struct.pack("<HH", 0, int(stale_release)))
        for name, value in {"CS": 0x1000, "DS": 0x3100, "SS": 0x3000, "ES": 0x4100,
                            "FS": 0x4200, "GS": 0x4300, "EAX": 0x11112222,
                            "EBX": 0x33334444, "ECX": 0x55556666, "EDX": 0x77778888,
                            "ESI": 0x12345678, "EDI": 0x23456789, "EBP": 0xd000,
                            "ESP": 0xe000, "EFLAGS": 0x603}.items():
            self.set_reg(name, value)
        expected = {name: self.reg(name) for name in REGISTERS}
        return_ip = (0xd75 if self.language == "ru" else 0x1250) if boundary else 0x2222
        self.u.mem_write(0x3e000, struct.pack("<HH", return_ip, 0x4000))
        self.u.emu_start(0x10000 + self.entry, 0x50100, count=100000)
        assert self.reg("CS") == 0x5000 and self.reg("IP") == 0x100
        # The original five bytes deliberately change AX, BP and SP. Compare
        # everything else, including high register halves and the direction bit.
        expected["EAX"] = expected["EAX"] & 0xffff0000 | 0x3000
        expected["EBP"] += 1
        expected["ESP"] -= 2
        expected["EFLAGS"] = (0x603 & ~(0x40 | 0x10 | 0x4 | 0x800)) | 0x80
        for name, value in expected.items():
            assert self.reg(name) == value, (operation, name, hex(self.reg(name)), hex(value))
        assert self.read16(0x3dffe) == 0xd001
        return self.read16(0x20014), self.read16(0x20016)


def checks(payload, language):
    count = 0
    machine = Machine(payload, language)
    for operation in range(2, 12):
        assert machine.call(operation)[1] == 0xfffb
        count += 1
    assert machine.call(1) == (0, 0)
    assert machine.call(2, a=b"A.TMP") == (6, 0)
    assert machine.call(2, a=b"A.TMP") == (80, 80)
    data = bytes(range(256)) * 16
    assert machine.call(4, handle=6, length=4096, data=data) == (4096, 0)
    for operation in (6, 10, 11, 7):
        assert machine.call(operation, handle=6)[1] == 0
    assert machine.call(8, a=b"A.TMP", b=b"A.SAV")[1] == 0
    assert machine.call(3, a=b"A.SAV") == (6, 0)
    assert machine.call(5, handle=6, length=4096) == (4096, 0)
    assert bytes(machine.u.mem_read(0x20400, 4096)) == data
    assert machine.call(5, handle=6, length=1) == (0, 0)
    assert machine.call(7, handle=6)[1] == 0
    assert machine.call(9, a=b"A.SAV")[1] == 0
    count += 16
    for operation in (4, 5):
        before = len(machine.calls)
        assert machine.call(operation, length=4097)[1] == 0xfffe
        assert len(machine.calls) == before
        count += 1
    assert machine.call(2, a=b"x" * 256)[1] == 0xfffd
    count += 1
    for failure in ("dos", "dpmi"):
        for operation in range(2, 12):
            machine.failure = failure
            result, error = machine.call(operation, handle=6, length=32, a=b"A.TMP", b=b"B.SAV")
            assert (result, error) == ((5, 5) if failure == "dos" else (0x8011, 0xffff))
            count += 1
    machine.failure = None
    assert machine.call(2, a=b"SHORT.TMP") == (6, 0)
    machine.failure = "short_write"
    assert machine.call(4, handle=6, length=100, data=b"x" * 100) == (50, 0)
    # A short write remains distinguishable from success to the transaction.
    assert len(machine.files[b"SHORT.TMP"]) == 50
    machine.failure = None
    assert machine.call(7, handle=6)[1] == 0
    assert machine.call(12)[1] == 0
    fresh = Machine(payload, language)
    fresh.failure = "dpmi"
    assert fresh.call(1) == (0x8011, 0xffff)
    assert fresh.read16(0x2001a) == 0
    count += 6
    safe = Machine(payload, language)
    safe.call(13)
    assert safe.held and safe.read16(0x20028) == 0xd000 and safe.read16(0x2002a) == 0x3100
    safe.call(13, stale_release=True)
    assert safe.held and safe.gate_release_flags == [0, 0]
    unsafe = Machine(payload, language)
    unsafe.call(13, boundary=False)
    assert not unsafe.held and unsafe.read16(0x20010) == 13
    rebased = Machine(payload, language)
    world_offset = 0x266a if language == "ru" else 0x271a
    clock_offset = 0x20c6 if language == "ru" else 0x2176
    rebased.u.mem_write(0x30000 + world_offset, struct.pack("<HH", 0, 0x7000))
    rebased.u.mem_write(0x30000 + clock_offset, struct.pack("<HII", 1, 1000, 777))
    for i, (selector, kind) in enumerate(((0x7100, 1), (0x7200, 8), (0x7300, 9), (0x7400, 3))):
        rebased.u.mem_write(0x7030e + 4 * i, struct.pack("<HH", 0, selector))
        rebased.write16((selector << 4) + 0x12, kind)
    for address, origin, active in ((0x71086, 2000, 1), (0x7206d, 0xfffffff0, 1),
                                     (0x73086, 3000, 0), (0x730b0, 4000, 1), (0x74086, 6000, 1)):
        rebased.u.mem_write(address, struct.pack("<HII", active, origin, 888))
    rebased.call(13)
    for address, expected in ((0x30000 + clock_offset + 2, 2234), (0x71088, 3234),
                              (0x7206f, (0xfffffff0 + 1234) & 0xffffffff),
                              (0x73088, 3000), (0x730b2, 5234), (0x74088, 6000)):
        assert struct.unpack("<I", rebased.u.mem_read(address, 4))[0] == expected
    assert struct.unpack("<I", rebased.u.mem_read(0x7108c, 4))[0] == 888
    return count + 4


def crc_checks(payload, language):
    machine = Machine(payload, language)
    entry, = struct.unpack_from("<H", payload, 14)
    checks = 0

    def update(data, state=0xffffffff, offset=1024, declared_size=None):
        u = machine.u
        u.mem_write(0x20000 + offset, data)
        for name, value in {"CS": 0x1000, "DS": 0x2000, "SS": 0x3000, "SP": 0xe000,
                            "SI": offset, "ECX": 0xa5a50000 | (len(data) if declared_size is None else declared_size),
                            "EBX": 0x12345678, "EDX": 0x23456789, "EAX": state, "EFLAGS": 0x602}.items():
            machine.set_reg(name, value)
        before = {name: machine.reg(name) for name in ("EBX", "ECX", "EDX", "ESI")}
        u.mem_write(0x3e000, struct.pack("<H", 0x8000))
        u.emu_start(0x10000 + entry, 0x18000, count=500000)
        assert machine.reg("IP") == 0x8000 and machine.reg("SP") == 0xe002
        assert all(machine.reg(name) == value for name, value in before.items())
        assert machine.reg("EFLAGS") & ~1 == 0x602
        return machine.reg("EAX"), machine.reg("EFLAGS") & 1

    for data in (b"", b"123456789", bytes(range(256)), bytes(range(256)) * 16):
        state, carry = update(data)
        assert carry == 0 and state ^ 0xffffffff == crc32(data)
        checks += 1
    state = 0xffffffff
    whole = b"123456789" * 1000
    for i in range(0, len(whole), 4096):
        state, carry = update(whole[i:i + 4096], state)
        assert carry == 0
    assert state ^ 0xffffffff == crc32(whole)
    assert update(b"x" * 16, offset=0xfff0)[0] ^ 0xffffffff == crc32(b"x" * 16)
    assert update(b"", offset=0xfff0, declared_size=17) == (0xffffffff, 1)
    assert update(b"", declared_size=4097) == (0xffffffff, 1)
    return checks + 4


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nasm", type=Path, default=ROOT / "build/tools/nasm-2.16.03/nasm.exe")
    args = parser.parse_args()
    work = ROOT / "build/save-research"
    work.mkdir(parents=True, exist_ok=True)
    for language in ("ru", "en"):
        destination = work / f"io-synthetic-{language}.bin"
        subprocess.run([str(args.nasm), "-f", "bin", "-I", str(ROOT / "src/wasdify/patches") + "/",
                        f"-DENG={int(language == 'en')}",
                        str(ROOT / "src/wasdify/patches/kremlin_save_io.asm"), "-o", str(destination)], check=True)
        payload = destination.read_bytes()
        print(language, checks(payload, language), "transport checks;",
              crc_checks(payload, language), "CRC checks passed")
