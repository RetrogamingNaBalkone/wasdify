"""Execute packaged HUD instructions on synthetic memory (Unicorn, dev only)."""
import argparse
import json
import struct

from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_CS, UC_X86_REG_DS, UC_X86_REG_ES,
                              UC_X86_REG_SS, UC_X86_REG_SP, UC_X86_REG_IP,
                              UC_X86_REG_BP, UC_X86_REG_SI, UC_X86_REG_DI,
                              UC_X86_REG_AX, UC_X86_REG_BX, UC_X86_REG_CX,
                              UC_X86_REG_DX)
from wasdify.patches.kremlin_numeric_hud import profiles


def verify(profile, values):
    u = Uc(UC_ARCH_X86, UC_MODE_16)
    u.mem_map(0, 0x100000)
    code, data, hooks = 0x10000, 0x20000, 0x40000
    offset = profile["offset"]
    u.mem_write(code + offset, bytes.fromhex(profile["replacement"]))
    for region in profile["regions"]:
        u.mem_write(code + region["offset"], bytes.fromhex(region["replacement"]))
    u.mem_write(code + profile["print_source"], struct.pack("<HH", 0x20, hooks >> 4))
    # Loader writes retained original fixups, which are now unreachable data.
    for kind, flags, source, target, target_offset in profile["fixups"]:
        if offset + 0x319 <= source < offset + 0x3a9:
            u.mem_write(code + source, struct.pack("<HH", 0x30, hooks >> 4))
        else:
            u.mem_write(code + source, b"\xcc\xcc\xcc\xcc" if kind == 3 else b"\xcc\xcc")
    en = profile["language"] == "en"
    surface_global, life_global, key_global = ((0xb53c, 0xa672, 0xa67a) if en else
                                              (0xb48c, 0xa5c2, 0xa5ca))
    for address, ptr in ((surface_global, 0x3000), (life_global, 0x3100),
                         (key_global, 0x3140), (life_global+20, 0x3200)):
        u.mem_write(data + address, struct.pack("<HH", ptr, data >> 4))
    u.mem_write(data + 0x3000, struct.pack("<H", 0x4000))
    u.mem_write(data + 0x4000, struct.pack("<HH", 0x1000, hooks>>4))
    u.mem_write(hooks + 0x1020, bytes([12]))
    u.mem_write(data + 0x4038, struct.pack("<HHHH", 0, hooks >> 4, 0x10, hooks >> 4))
    u.mem_write(data + 0x4034, struct.pack("<HH", 0x40, hooks >> 4))
    u.mem_write(data + 0x3261, struct.pack("<HH", 0x3400, data >> 4))
    u.mem_write(data + 0x3300, struct.pack("<HH", 12, 8))
    calls = []

    def read_words(address, count):
        return struct.unpack("<" + "H" * count, u.mem_read(address, count * 2))

    def observe(machine, address, size, _):
        if address in (code + 0x7000, code + offset + 0x3a9):
            machine.emu_stop()
            return
        if address not in (hooks, hooks + 0x10, hooks + 0x20, hooks + 0x30, hooks + 0x40):
            return
        sp = machine.reg_read(UC_X86_REG_SP)
        stack = data + sp
        if address == hooks + 0x20:
            surface, selector, x, y, ptr, sel = read_words(stack + 4, 6)
            string = bytes(machine.mem_read((sel << 4) + ptr, 6)).split(b"\0", 1)[0].decode("ascii")
            calls.append(("number", x, y, string))
        elif address in (hooks, hooks+0x40):
            fields = struct.unpack("<HH4I", machine.mem_read(stack + 4, 20))
            calls.append(("clear" if address == hooks else "frame", *fields[2:]))
        elif address == hooks+0x30:
            calls.append(("dirty", *read_words(stack+4, 4)))
        else:
            fields = struct.unpack("<HHIIHHHH", machine.mem_read(stack + 4, 20))
            calls.append(("post" if fields[4] == 0x3400 else "icon", fields[2], fields[3], fields[-2], fields[-1]))
        # Native cdecl methods may destroy these registers; SI/DI/BP/DS survive.
        for reg in (UC_X86_REG_AX, UC_X86_REG_BX, UC_X86_REG_CX, UC_X86_REG_DX):
            machine.reg_write(reg, 0xdead)
        machine.reg_write(UC_X86_REG_ES, 0x5000)
        ip, cs = read_words(stack, 2)
        machine.reg_write(UC_X86_REG_SP, sp + 4)
        machine.reg_write(UC_X86_REG_CS, cs)
        machine.reg_write(UC_X86_REG_IP, ip)

    u.hook_add(UC_HOOK_CODE, observe)
    count = 0
    for width, height, font_width in ((320, 200, 8), (640, 400, 16), (640, 480, 16),
                                      (800, 600, 24), (1024, 768, 24)):
        x, top = 11 * width // 320, 177 * height // 200
        box_width, box_height = 53 * width // 320, 18 * height // 200
        u.mem_write(data + 0x3022, struct.pack("<H", font_width))
        u.mem_write(data + 0x3140, struct.pack("<6H", 201*width//320, top,
                                            254*width//320, top+box_height, box_width, box_height))
        # Deliberately start close to a 64-KB boundary, exercising independent
        # source/destination wraps and wraps within the copied pillar itself.
        row_addresses = {}
        for y in range(top, top+box_height+1):
            position = 0xff00+(y-top)*width
            address = 0x50000+position
            row_addresses[y] = address
            u.mem_write(address, bytes((13*x+y)%251 for x in range(width)))
            u.mem_write(data+surface_global+0xb6+4*y,
                        struct.pack("<HH", position & 0xffff, 0x5000+(position>>16)*0x1000))
        for charge in (False, True):
            left = x + (box_width + box_width // 4 if charge else 0)
            for value in values:
                u.mem_write(data + 0x3100, struct.pack("<6H", x, top, x+130, top+8, 130, 8))
                u.mem_write(data + 0x3120, struct.pack("<6H", x, top+12, x+130, top+20, 130, 8))
                u.mem_write(data + 0xf000, struct.pack("<11H", 0x7000, code >> 4,
                            0x3200, data >> 4, 0x3120 if charge else 0x3100, data >> 4,
                            0x3300, data >> 4, 100, value, 10))
                for reg, v in ((UC_X86_REG_CS, code >> 4), (UC_X86_REG_SS, data >> 4),
                               (UC_X86_REG_DS, 0x5000), (UC_X86_REG_SP, 0xf000),
                               (UC_X86_REG_BP, 0x8000), (UC_X86_REG_SI, 0x1234),
                               (UC_X86_REG_DI, 0x5678)):
                    u.reg_write(reg, v)
                calls.clear()
                u.emu_start(code + offset, code + 0x7001, count=100000)
                assert len(calls) == 4, calls
                assert calls[0] == ("clear", left, top, left+box_width, top+box_height), calls
                icon_height=box_height*3//8
                icon_width=icon_height*12//8
                assert calls[1] == ("icon", left+(box_width-icon_width)//2, top, icon_width, icon_height), calls
                assert calls[2] == ("number", left+(box_width-len(str(value))*font_width)//2,
                                    top+box_height//2, str(value)), calls
                assert calls[3] == ("dirty", 0x3002, data>>4, 0x3120 if charge else 0x3100, data>>4), calls
                source_x=201*width//320-box_width//4+1
                for y,address in row_addresses.items():
                    expected=bytes((13*x+y)%251 for x in range(source_x, source_x+box_width//4))
                    assert bytes(u.mem_read(address+left+box_width,box_width//4))==expected, (width,y)
                assert u.reg_read(UC_X86_REG_SP) == 0xf004
                assert u.reg_read(UC_X86_REG_BP) == 0x8000
                assert u.reg_read(UC_X86_REG_SI) == 0x1234
                assert u.reg_read(UC_X86_REG_DI) == 0x5678
                assert u.reg_read(UC_X86_REG_DS) == 0x5000
                count += 1
    # Execute the compacted native setters, including all pre-existing dirty bits.
    for region, field, mask in zip(profile["regions"][1:], (0x12, 0x16, 0x14, 0x18, 0x22), (6, 10, 6, 10, 0x402)):
        for value in (0, 1, 100, 32767, 65535):
            u.mem_write(data+0x3210, struct.pack("<H", 0xa550))
            u.mem_write(data+0xf000, struct.pack("<5H", 0x7000, code>>4, 0x3200, data>>4, value))
            for reg, v in ((UC_X86_REG_CS, code>>4), (UC_X86_REG_SS, data>>4),
                           (UC_X86_REG_DS, 0x5000), (UC_X86_REG_SP, 0xf000),
                           (UC_X86_REG_BP, 0x8000), (UC_X86_REG_SI, 0x1234), (UC_X86_REG_DI, 0x5678)):
                u.reg_write(reg, v)
            u.emu_start(code+region["offset"],code+0x7001,count=100)
            assert read_words(data+0x3210,1)[0] == 0xa550 | mask
            assert read_words(data+0x3200+field,1)[0] == value
            for reg, expected in ((UC_X86_REG_SP, 0xf004), (UC_X86_REG_BP, 0x8000),
                                  (UC_X86_REG_SI,0x1234),(UC_X86_REG_DI,0x5678),(UC_X86_REG_DS,0x5000)):
                assert u.reg_read(reg)==expected
            count += 1
    # Execute the enclosing caller's two-field dispatch as well as direct calls.
    u.mem_write(data+life_global+4,struct.pack("<HH",0x3120,data>>4))
    for member in (0x65,0x69):
        u.mem_write(data+0x3200+member,struct.pack("<HH",0x3300,data>>4))
    u.mem_write(data+0x3212,struct.pack("<H",73))
    u.mem_write(data+0x3216,struct.pack("<H",42))
    u.mem_write(data+0x8006,struct.pack("<HH",0x3200,data>>4))
    for reg,v in ((UC_X86_REG_CS,code>>4),(UC_X86_REG_SS,data>>4),(UC_X86_REG_DS,data>>4),
                  (UC_X86_REG_BP,0x8000),(UC_X86_REG_SP,0xe000)):
        u.reg_write(reg,v)
    calls.clear()
    u.emu_start(code+offset+0x319,code+offset+0x3aa,count=200000)
    assert [c[3] for c in calls if c[0]=="number"]==["73","42"], calls
    assert u.reg_read(UC_X86_REG_SP)==0xe000
    count+=1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exhaustive", action="store_true", help="Every unsigned 16-bit value in all layouts")
    args = parser.parse_args()
    values = (list(range(65536)) if args.exhaustive else
              sorted(set(range(0, 1025, 17)) | {0, 1, 9, 10, 99, 100, 999, 1000, 9999, 10000, 32767, 65535}))
    for profile in profiles():
        print(json.dumps({"language": profile["language"], "scenarios": verify(profile, values)}), flush=True)


if __name__ == "__main__":
    main()

