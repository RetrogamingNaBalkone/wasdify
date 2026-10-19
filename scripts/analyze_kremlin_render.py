"""Differential execution of complete published span contexts on synthetic data.

Development-only Unicorn 2.1.4; no game files are read or written. Real-mode
segment bases model contiguous 64 KiB huge-pointer windows. The imported
selector shift and increment are supplied as 12 and 0x1000 respectively;
actual DPMI descriptors and the DOS loader require an in-game check.
"""
from itertools import product
import json
import struct

from wasdify.patches.kremlin_render import profiles


def verify():
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
    from unicorn import x86_const as x

    registers = [getattr(x, "UC_X86_REG_" + name) for name in
                 ("EAX", "EBX", "ECX", "EDX", "ESI", "EDI", "EBP", "ESP", "DS", "ES", "FS", "GS", "SS", "EFLAGS")]
    count = 0
    for profile in profiles():
        original = bytearray(int(b, 16) if b != "??" else 0 for b in profile["signature"].split())
        for region in profile["regions"]:
            old = bytes.fromhex(region["expected"])
            original[region["relative"]:region["relative"] + len(old)] = old
        patched = original.copy()
        for region in profile["regions"]:
            new = bytes.fromhex(region["replacement"])
            patched[region["relative"]:region["relative"] + len(new)] = new
        # Loader import KERNEL ordinal 113 supplies huge-pointer selector shift.
        struct.pack_into("<H", original, 0x22, 12)
        struct.pack_into("<H", patched, 0x22, 12)
        delta = 0 if profile["language"] == "ru" else 0xb0
        for length, destination, fraction in product((1, 2, 3, 16, 320, 640, 800, 1024),
                                                     (0, 0xfffe, 0xffff, 0x10000 - 320),
                                                     (0, 0x8000, 0xffff)):
            results = []
            for code in (original, patched):
                cpu = Uc(UC_ARCH_X86, UC_MODE_16)
                cpu.mem_map(0, 0x90000)
                for reg, value in ((x.UC_X86_REG_CS, 0x1000), (x.UC_X86_REG_DS, 0x3000),
                                   (x.UC_X86_REG_SS, 0x3000), (x.UC_X86_REG_ES, 0x6000),
                                   (x.UC_X86_REG_SP, 0xf000), (x.UC_X86_REG_BP, 0xe123)):
                    cpu.reg_write(reg, value)
                cpu.mem_write(0x10000, bytes(code))
                cpu.mem_write(0x3f000, struct.pack("<HH", 0xfff0, 0x1000))
                cpu.mem_write(0x40000, bytes(range(256)) * 256)
                cpu.mem_write(0x50000, bytes((i * 73 + 11) % 256 for i in range(256)))
                cpu.mem_write(0x60000, b"\xa5" * 0x20000)

                def word(address, value):
                    cpu.mem_write(0x30000 + address + delta, struct.pack("<H", value))

                def dword(address, value):
                    cpu.mem_write(0x30000 + address + delta, struct.pack("<I", value))

                word(0xc1da, 0)
                word(0xc1dc, length - 1)
                word(0xc1d8, 0)
                word(0xc14a, 64)
                word(0xc14c, 0xff80)
                word(0xc14e, 0x4000)
                word(0xc144, 0x1000)
                word(0xb526, 0x5000)
                dword(0xc1e0, fraction)
                dword(0xc1e4, fraction)
                dword(0xc1e8, 0x10000 + fraction)
                dword(0xc1ec, fraction)
                cpu.mem_write(0x30000 + 0xb542 + delta, struct.pack("<HH", destination, 0x6000))
                accesses, instructions = [], [0]

                def memory(uc, access, address, size, value, user):
                    accesses.append((access, address, size, value if access == UC_MEM_WRITE else 0))

                def instruction(uc, address, size, user):
                    instructions[0] += 1
                    if address in (0x100b9, 0x100d1):
                        uc.emu_stop()  # Verify the common RETF preconditions below.

                cpu.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, memory)
                cpu.hook_add(UC_HOOK_CODE, instruction)
                cpu.emu_start(0x10000, 0x1fff0, count=30000)
                assert cpu.reg_read(x.UC_X86_REG_IP) in (0xb9, 0xd1)
                assert cpu.reg_read(x.UC_X86_REG_SP) == 0xf000
                assert bytes(cpu.mem_read(0x3f000, 4)) == struct.pack("<HH", 0xfff0, 0x1000)
                results.append((bytes(cpu.mem_read(0x30000, 0x50000)),
                                tuple(cpu.reg_read(r) for r in registers), accesses, instructions[0]))
            assert results[0][:3] == results[1][:3], (profile["language"], length, destination, fraction)
            saved = results[0][3] - results[1][3]
            assert saved == 2 * length, (length, saved)
            count += 1
    return {"cases": count, "versions": ["ru", "en"], "function_body_and_epilogue_equivalent": True,
            "retf_preconditions_verified": True, "retf_executed": False,
            "memory_accesses_identical": True, "instructions_saved_per_pixel": 2,
            "in_game_fps_measured": False}


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))

