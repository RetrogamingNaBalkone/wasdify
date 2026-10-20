"""Execute font payloads against a synthetic raster with Unicorn (dev only)."""
import struct

from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_CS, UC_X86_REG_DS, UC_X86_REG_ES,
                              UC_X86_REG_SS, UC_X86_REG_SP, UC_X86_REG_IP,
                              UC_X86_REG_BP, UC_X86_REG_SI, UC_X86_REG_DI,
                              UC_X86_REG_AX, UC_X86_REG_BX, UC_X86_REG_CX,
                              UC_X86_REG_DX)
from wasdify.patches.kremlin_font_scaling import profiles


def verify(profile):
    u = Uc(UC_ARCH_X86, UC_MODE_16)
    u.mem_map(0, 0x100000)
    code, data, hook = 0x10000, 0x20000, 0x40000
    glyph, loader = profile["regions"][:2]
    origin = glyph["offset"]
    u.mem_write(code+origin, bytes.fromhex(glyph["replacement"]))
    u.mem_write(code+loader["offset"], bytes.fromhex(loader["replacement"]))
    u.mem_write(data+0x3000, struct.pack("<H", 0x4000))
    u.mem_write(data+0x301a, struct.pack("<HH", 0x5000, data >> 4))
    u.mem_write(data+0x301e, struct.pack("<HH", 0x003d, 0x0012))
    u.mem_write(data+0x4004, struct.pack("<HH", 0, hook >> 4))
    # Non-symmetric pixels exercise both axes, every bit, and high character IDs.
    raster = bytes((x*73+19) & 255 for x in range(2048))
    u.mem_write(data+0x5000, raster)
    pixels = []

    def observe(machine, address, size, _):
        if address in (code+0x7000, code+loader["offset"]+30):
            machine.emu_stop()
        elif address == hook:
            sp = machine.reg_read(UC_X86_REG_SP)
            ip, cs, obj, sel, x, y, color = struct.unpack(
                "<HHHHiiH", machine.mem_read(data+sp, 18))
            assert (obj, sel) == (0x3000, data >> 4)
            pixels.append((x, y, color & 255))
            for reg in (UC_X86_REG_AX, UC_X86_REG_BX, UC_X86_REG_CX, UC_X86_REG_DX):
                machine.reg_write(reg, 0xdead)
            machine.reg_write(UC_X86_REG_ES, 0x5000)
            machine.reg_write(UC_X86_REG_SP, sp+4)
            machine.reg_write(UC_X86_REG_CS, cs)
            machine.reg_write(UC_X86_REG_IP, ip)

    u.hook_add(UC_HOOK_CODE, observe)

    def reset():
        for reg, value in ((UC_X86_REG_CS, code >> 4), (UC_X86_REG_DS, 0x5000),
                           (UC_X86_REG_SS, data >> 4), (UC_X86_REG_SP, 0x9000),
                           (UC_X86_REG_BP, 0x8500), (UC_X86_REG_SI, 0x1234),
                           (UC_X86_REG_DI, 0x5678)):
            u.reg_write(reg, value)

    scenarios = 0
    for width, height in ((320, 200), (640, 400), (640, 480), (800, 600), (1024, 768)):
        u.mem_write(data+0x3030, struct.pack("<II", width, height))
        reset()
        u.reg_write(UC_X86_REG_BP, 0x8ffa)
        u.mem_write(data+0x9000, struct.pack("<HH", 0x3000, data >> 4))
        u.emu_start(code+loader["offset"], code+0x8000, count=1000)
        fw, fh = struct.unpack("<HH", u.mem_read(data+0x3022, 4))
        assert (fw, fh) == (width//40, height//25)
        for char in (0, 48, 65, 127, 128, 255):
            for transparent in (0, 1):
                for x, y, clip in ((20, 30, (0, 0, width-1, height-1)),
                                    (-5, -3, (0, 0, 10, 7)),
                                    (width-4, height-3, (0, 0, width-1, height-1)),
                                    (20, 30, (23, 32, 26, 35))):
                    u.mem_write(data+0x3038, struct.pack("<4i", *clip))
                    reset()
                    u.mem_write(data+0x9000, struct.pack("<HHHHiiHH", 0x7000, code >> 4,
                                0x3000, data >> 4, x, y, char, transparent))
                    pixels.clear()
                    u.emu_start(code+origin, code+0x8000, count=1000000)
                    expected = []
                    for dy in range(fh):
                        row = raster[char*8+dy*8//fh]
                        for dx in range(fw):
                            bit = row & (0x80 >> (dx*8//fw))
                            if not bit and transparent:
                                continue
                            if clip[0] <= x+dx <= clip[2] and clip[1] <= y+dy <= clip[3]:
                                expected.append((x+dx, y+dy, 0x3d if bit else 0x12))
                    assert pixels == expected, (profile["language"], width, height, char, x, y)
                    for reg, value in ((UC_X86_REG_SP, 0x9004), (UC_X86_REG_BP, 0x8500),
                                       (UC_X86_REG_SI, 0x1234), (UC_X86_REG_DI, 0x5678),
                                       (UC_X86_REG_DS, 0x5000)):
                        assert u.reg_read(reg) == value, (reg, u.reg_read(reg), value)
                    scenarios += 1
    print(profile["language"], scenarios, "raster/clip/stack scenarios passed")


if __name__ == "__main__":
    for profile in profiles():
        verify(profile)
