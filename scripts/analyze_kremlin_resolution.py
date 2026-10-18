"""Read-only research check of real selector instructions; requires Unicorn 2.1.4.

Usage: python scripts/analyze_kremlin_resolution.py path/to/PKR.EXE
Unicorn is an analysis dependency only, never an application dependency.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wasdify.engine import build_result
from wasdify.patches.kremlin_resolution import Resolution640x400, locate


def verify(path):
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_HOOK_CODE
    from unicorn.x86_const import (UC_X86_REG_BP, UC_X86_REG_SP,
                                  UC_X86_REG_AX, UC_X86_REG_IP)
    source = path.read_bytes()
    offset = locate(source)
    if source[offset] != 1:
        raise ValueError("Supply an original, unpatched executable")
    analysis = Resolution640x400().analyze({"main": source})
    patched = build_result({"main": source}, analysis.changes)["main"]
    assert len(source) == len(patched)
    assert [i for i, (a, b) in enumerate(zip(source, patched)) if a != b] == [offset]
    rows = []
    for state, data in (("original", source), ("patched", patched)):
        for available in (True, False):
            for argument in (1, 2, 3, 4, 5):
                cpu = Uc(UC_ARCH_X86, UC_MODE_16)
                cpu.mem_map(0, 0x10000)
                cpu.mem_write(0x2000, data[offset - 15:offset - 15 + 100])
                cpu.reg_write(UC_X86_REG_BP, 0x8000)
                cpu.reg_write(UC_X86_REG_SP, 0x9000)
                cpu.mem_write(0x8006, struct.pack("<H", argument))
                calls = []
                def intercept(uc, address, size, user):
                    if address not in (0x1DFB, 0x25EF):
                        return
                    sp = uc.reg_read(UC_X86_REG_SP)
                    if address == 0x1DFB:
                        uc.reg_write(UC_X86_REG_AX, int(available))
                    else:
                        calls.append(struct.unpack("<H", uc.mem_read(sp + 4, 2))[0])
                    ip = struct.unpack("<H", uc.mem_read(sp, 2))[0]
                    uc.reg_write(UC_X86_REG_SP, sp + 4)
                    uc.reg_write(UC_X86_REG_IP, ip)
                cpu.hook_add(UC_HOOK_CODE, intercept)
                cpu.emu_start(0x2000, 0x2064, count=200)
                modes = (0x101 if state == "original" else 0x100, 0x101, 0x103, 0x105)
                expected = [modes[argument - 1]] if available and argument < 5 else []
                assert calls == expected, (state, available, argument, calls, expected)
                assert cpu.reg_read(UC_X86_REG_IP) == 0x2064
                assert cpu.reg_read(UC_X86_REG_SP) == 0x9000
                rows.append({"state": state, "vesa": available, "argument": argument,
                             "modes": [hex(mode) for mode in calls]})
    assert path.read_bytes() == source
    return {"source_sha256": hashlib.sha256(source).hexdigest(),
            "output_sha256": hashlib.sha256(patched).hexdigest(),
            "size": len(source), "patch_file_offset": hex(offset),
            "scenarios": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.exe), indent=2))
