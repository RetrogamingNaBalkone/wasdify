"""Execute only WASDify's own observer stubs against synthetic Unicorn memory."""
import json
from pathlib import Path
import struct
import subprocess
import sys

from profile_kremlin import ROOT, observer_asm


def main():
    sys.path.insert(0, str(ROOT / "build/research-deps"))
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_HOOK_CODE
    from unicorn import x86_const as x
    work = ROOT / "build/profiler-verify"
    work.mkdir(parents=True, exist_ok=True)
    nasm = ROOT / "build/tools/nasm-2.16.03/nasm.exe"
    reg_names = ("EAX", "EBX", "ECX", "EDX", "ESI", "EDI", "EBP", "ESP", "DS", "ES", "FS", "GS", "SS", "EFLAGS")
    regs = [getattr(x, "UC_X86_REG_" + n) for n in reg_names]
    checked = 0
    for entering in (True, False):
        asm = work / f"{entering}.asm"
        binary = asm.with_suffix(".bin")
        asm.write_text(observer_asm(0x1000, 0, 32, entering), encoding="ascii")
        subprocess.run([str(nasm), "-f", "bin", str(asm), "-o", str(binary)], check=True)
        instrumented = bytearray(binary.read_bytes())
        instrumented[8:10] = struct.pack("<H", 0x3000)  # Synthetic data segment.
        original = bytes.fromhex("8c d0 90") if entering else bytes.fromhex("5d 4d cb")
        for flags in (0x202, 0x246, 0x697, 0xe03):
            snapshots = []
            for observer in (False, True):
                cpu = Uc(UC_ARCH_X86, UC_MODE_16)
                cpu.mem_map(0, 0x60000)
                for name, value in {"CS": 0x1000, "SS": 0x2000, "DS": 0x4000,
                                    "ES": 0x5000, "FS": 0x4000, "GS": 0x5000,
                                    "EAX": 0xaabbccdd, "EBX": 0x12345678,
                                    "ECX": 0x10293847, "EDX": 0x87654321,
                                    "ESI": 0x11112222, "EDI": 0x33334444,
                                    "EBP": 0x55556666, "ESP": 0xf000,
                                    "EFLAGS": flags}.items():
                    cpu.reg_write(getattr(x, "UC_X86_REG_" + name), value)
                cpu.mem_write(0x2f000, struct.pack("<HHH", 0x7654, 0xaaaa, 0x1000))
                cpu.mem_write(0x30020, struct.pack("<6I", 7, 123, 0xfffffff0, 2, 6, 0))
                code = bytes(instrumented) if observer else original
                address = 0x11000 if observer else 0x10000
                cpu.mem_write(address, code)

                def hook(uc, at, size, _):
                    ins = bytes(uc.mem_read(at, size))
                    if ins == b"\x0f\x31":
                        uc.reg_write(x.UC_X86_REG_EAX, 1000)
                        uc.reg_write(x.UC_X86_REG_EDX, 0)
                        uc.reg_write(x.UC_X86_REG_IP, (at & 0xffff) + 2)
                    if (entering and at == 0x10003) or (not entering and ins == b"\xcb"):
                        uc.emu_stop()

                cpu.hook_add(UC_HOOK_CODE, hook)
                cpu.emu_start(address, 0x12000, count=100)
                snapshots.append([cpu.reg_read(r) for r in regs])
                if observer:
                    values = struct.unpack("<6I", cpu.mem_read(0x30020, 24))
                    expected = (8, 1000, 0xfffffff0, 2, 6, 0) if entering else (7, 123, 861, 3, 7, 0)
                    assert values == expected, (values, expected)
            assert snapshots[0] == snapshots[1], (entering, flags, dict(zip(reg_names, zip(*snapshots))))
            checked += 1
    print(json.dumps({"synthetic_observer_cases": checked, "registers_flags_stack_and_carry": "ok"}))


if __name__ == "__main__":
    main()
