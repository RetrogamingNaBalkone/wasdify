"""Execute the autonomous serializer against invented memory, never game files."""
import json
from pathlib import Path
import struct
from zlib import crc32

from build_kremlin_save_native import build, ROOT
from build_kremlin_save_native import layout_fields
from verify_kremlin_save_io_synthetic import Machine


class NativeMachine(Machine):
    def __init__(self, payload):
        super().__init__(payload, 'ru')
        at = payload.index(b'KRSNAT01') + 8
        names = ('frame', 'early', 'death', 'capture', 'validate', 'restore', 'write', 'read')
        self.entries = dict(zip(names, struct.unpack_from('<8H', payload, at)))
        self.write16(0x31fae, 2)
        self.u.mem_write(0x3266a, struct.pack('<HH', 0, 0x7000))
        self.write16(0x70000, 64)
        self.write16(0x70004, 64)
        self.u.mem_write(0x70442, struct.pack('<HH', 0, 0x7100))
        self.u.mem_write(0x3a5d6, struct.pack('<HH', 0, 0x7300))
        self.u.mem_write(0x32d7e, bytes((77,)) * 4096)
        for offset in (0x608a, 0x718e, 0x8292):
            self.u.mem_write(0x30000 + offset, bytes((77,)) * 4096)
        self.write16(0x71002, 4)
        self.write16(0x71012, 1)
        for offset, value in ((0x5e, 20), (0x60, 50), (0x62, 100), (0x64, 100), (0xd6, 3)):
            self.write16(0x71000 + offset, value)
        self.u.mem_write(0x20034, struct.pack('<I', 10000))
        self.write16(0x20018, 0x6000)
        self.write16(0x2001a, 0x6000)
        # Let the actual frame code build the fresh bindings, without disk/menu
        # work: its only purpose in this call is to enter an invented level.
        self.write16(0x200c2, 1)
        self.invoke('frame')
        self.failure_point = None
        self.operation_counts = {}
        self.terminated = False

    def interrupt(self, u, number, data):
        if number == 0x21:
            assert self.reg('AX') == 0x4c01
            self.terminated = True
            u.emu_stop()
            return
        previous = self.failure
        if number == 0x31 and self.reg('AX') == 0x300:
            operation = self.read16(0x20040 + 28) >> 8
            ordinal = self.operation_counts.get(operation, 0) + 1
            self.operation_counts[operation] = ordinal
            if self.failure_point == (operation, ordinal):
                self.failure = 'dos'
        super().interrupt(u, number, data)
        self.failure = previous

    def observe(self, u, address, size, data):
        super().observe(u, address, size, data)
        if address in (0x50500, 0x50900):
            sp = self.reg('SP')
            ip, cs = struct.unpack('<HH', u.mem_read(0x30000 + sp, 4))
            self.set_reg('SP', sp + 4)
            self.set_reg('CS', cs)
            self.set_reg('IP', ip)

    def invoke(self, name, *, expect_exit=False):
        for register, value in {'CS': 0x1000, 'DS': 0x2000, 'SS': 0x3000,
                'ES': 0x7100, 'FS': 0x8000, 'GS': 0x7000, 'SP': 0xd000,
                'BP': 0, 'EFLAGS': 0x202}.items():
            self.set_reg(register, value)
        self.write16(0x3d000, 0xff00)
        self.write16(0x200c0, 0xd000)
        self.write16(0x200f2, 0)
        self.u.emu_start(0x10000 + self.entries[name], 0x1ff00, count=3000000)
        if expect_exit:
            assert self.terminated, name
            return False
        assert self.reg('IP') == 0xff00, (name, hex(self.reg('CS')), hex(self.reg('IP')))
        return self.read16(0x200f2) == 0 and not self.reg('EFLAGS') & 1

    def snapshot(self):
        size = self.read16(0x8001c)
        return bytes(self.u.mem_read(0x80000, size))

    def install(self, raw):
        self.u.mem_write(0x80000, bytes(raw))
        self.write16(0x200e8, len(raw))


def run(payload):
    m = NativeMachine(payload)
    assert m.invoke('capture')
    raw = m.snapshot()
    assert m.invoke('validate')
    checksum = struct.unpack_from('<I', raw, 40)[0]
    wire = bytearray(raw)
    wire[40:44] = bytes(4)
    assert checksum == crc32(wire)
    assert m.invoke('write')
    assert bytes(m.files[b'KRN1.SAV']) == raw
    # A second write preserves the verified old generation, then replaces SAV.
    m.write16(0x7105e, 45)
    assert m.invoke('capture')
    latest = m.snapshot()
    assert m.invoke('write')
    assert bytes(m.files[b'KRN1.PRV']) == raw
    assert bytes(m.files[b'KRN1.SAV']) == latest
    checks = 5
    # Restore into different native allocations and verify every scalar by
    # the guest's own recapture, including fresh HUD bindings.
    target = NativeMachine(payload)
    target.u.mem_write(0x72000, bytes(target.u.mem_read(0x71000, 256)))
    target.u.mem_write(0x70442, struct.pack('<HH', 0, 0x7200))
    target.u.mem_write(0x200c4, bytes(4))
    target.invoke('frame')
    target.install(raw)
    assert target.invoke('validate')
    target.write16(0x7205e, 1)
    assert target.invoke('restore')
    assert target.read16(0x7205e) == 20
    assert target.snapshot() == raw
    checks += 1
    # Reject scalar/ID/schema/length corruption before native world mutation.
    for offset in (0, 8, 24, 28, 30, 32, 34, 40, 44, 64, len(raw) - 1):
        changed = bytearray(raw)
        changed[offset] ^= 0x80
        m.install(changed)
        before = bytes(m.u.mem_read(0x70000, 0x2000))
        assert not m.invoke('validate'), offset
        assert bytes(m.u.mem_read(0x70000, 0x2000)) == before
        checks += 1
    # Check semantic validation even when a corrupt producer recomputes CRC.
    record = 64 + 16384
    field_offsets = {}
    position = record + 16
    for name, (_, wire) in layout_fields(1, 2).items():
        field_offsets[name] = position
        position += struct.calcsize('<' + wire)
    for name, wire, bad in (('health', 'h', 0), ('health', 'h', 101), ('energy', 'h', -1),
            ('lives', 'h', 0), ('selected_weapon', 'h', 9), ('selected_weapon', 'h', 1),
            ('pending_weapon', 'h', 1), ('heading', 'h', 360), ('x', 'i', -1), ('z', 'i', 30720)):
        changed = bytearray(raw)
        struct.pack_into('<' + wire, changed, field_offsets[name], bad)
        changed[40:44] = bytes(4)
        struct.pack_into('<I', changed, 40, crc32(changed))
        m.install(changed)
        assert not m.invoke('validate'), (name, bad)
        checks += 1
    for failure in ('short_write', 'dos', 'dpmi'):
        m.install(raw)
        m.failure = failure
        assert not m.invoke('write')
        assert bytes(m.files[b'KRN1.SAV']) == latest
        assert bytes(m.files[b'KRN1.PRV']) == raw
        m.failure = None
        checks += 1
    # Each real DOS operation has independent failure paths, including both
    # renames. At least the previously verified current generation survives.
    for operation, ordinal in ((0x40, 1), (0x40, 3), (0x68, 1), (0x3e, 1),
            (0x3f, 1), (0x3f, 2), (0x41, 1), (0x56, 1), (0x56, 2)):
        trial = NativeMachine(payload)
        trial.install(raw)
        trial.files = {b'KRN1.SAV': bytearray(latest), b'KRN1.PRV': bytearray(raw)}
        trial.failure_point = operation, ordinal
        assert not trial.invoke('write'), (operation, ordinal)
        assert any(bytes(trial.files.get(name, b'')) == latest for name in (b'KRN1.SAV', b'KRN1.PRV'))
        checks += 1
    trial = NativeMachine(payload)
    trial.install(raw)
    trial.files[b'KRN1.TMP'] = bytearray(b'foreign temporary file')
    assert not trial.invoke('write')
    assert trial.files[b'KRN1.TMP'] == b'foreign temporary file'
    checks += 1
    trial = NativeMachine(payload)
    corrupt = bytearray(raw)
    corrupt[0] ^= 1
    trial.install(corrupt)
    trial.write16(0x200ca, 1)  # a native loader already started replacing the world
    trial.invoke('validate', expect_exit=True)
    checks += 1
    # A pending F5 belongs to the dying hero even if no file exists. The death
    # gate must cancel it before replaying the exact native health prologue.
    trial = NativeMachine(payload)
    entry = struct.unpack_from('<H', payload, payload.index(b'KRSLIVE1') + 8)[0]
    trial.write16(0x200c8, 3)
    trial.write16(0x200e4, 1)
    trial.write16(0x20090, 0)
    trial.write16(0x7105e, 0)
    for register, value in {'CS': 0x1000, 'SS': 0x3000, 'SP': 0xd000,
            'DS': 0x3000, 'BP': 0xc000, 'EFLAGS': 0x202}.items():
        trial.set_reg(register, value)
    trial.u.mem_write(0x3d000, struct.pack('<4H', 0x1005, 0x4000, 0, 0x7100))
    trial.u.emu_start(0x10000 + entry, 0x50800, count=10000)
    assert trial.reg('CS') == 0x5000 and trial.reg('IP') == 0x800
    assert trial.read16(0x200c8) == trial.read16(0x200e4) == 0
    checks += 1
    return {'checks': checks, 'snapshot_size': len(raw), 'status': 'passed'}


if __name__ == '__main__':
    output = ROOT / 'build/save-research/native-synthetic.bin'
    build(ROOT / 'build/tools/nasm-2.16.03/nasm.exe', output)
    print(json.dumps(run(output.read_bytes()), indent=2))

