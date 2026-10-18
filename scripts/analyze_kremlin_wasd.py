"""Read-only execution of real input instructions; requires Unicorn 2.1.4.

PYTHONPATH=src;<research-dependencies> python scripts/analyze_kremlin_wasd.py EXE
Does not launch the game or write an executable. Collisions and clock calls in
the player test are intercepted; mouse-driver interrupts are controlled inputs.
"""
import argparse
import hashlib
from itertools import product
from pathlib import Path
import struct

from wasdify.engine import build_result
from wasdify.patches.kremlin_wasd import WasdControls, profiles, segments, locate


def verify(path):
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_HOOK_CODE, UC_HOOK_INTR
    from unicorn.x86_const import (UC_X86_REG_AX, UC_X86_REG_BX, UC_X86_REG_CX,
                                  UC_X86_REG_DX, UC_X86_REG_CS, UC_X86_REG_DS,
                                  UC_X86_REG_ES, UC_X86_REG_SS, UC_X86_REG_SP,
                                  UC_X86_REG_BP, UC_X86_REG_IP, UC_X86_REG_SI,
                                  UC_X86_REG_DI)
    original = path.read_bytes()
    analysis = WasdControls().analyze({"main": original})
    if analysis.already_applied:
        raise ValueError("Supply an original executable")
    data = build_result({"main": original}, analysis.changes)["main"]
    candidates = []
    for profile in profiles():
        try:
            candidates.append((profile, locate(data, profile, segments(data))))
        except Exception:
            pass
    profile, found = candidates[0]
    inp = profile["anchors"]["input"]["offset"]
    player = profile["anchors"]["player"]["offset"]
    delta = player - 0xa85
    count = 0

    def cpu_for(name, selector=0x1000):
        cpu = Uc(UC_ARCH_X86, UC_MODE_16)
        cpu.mem_map(0, 0x80000)
        segment = found[name]
        cpu.mem_write(selector * 16, data[segment.start:segment.end])
        for reg, value in ((UC_X86_REG_CS, selector), (UC_X86_REG_DS, 0x3000),
                           (UC_X86_REG_SS, 0x3000), (UC_X86_REG_ES, 0x4000),
                           (UC_X86_REG_BP, 0x8000), (UC_X86_REG_SP, 0x7f00)):
            cpu.reg_write(reg, value)
        cpu.mem_write(0x38006, struct.pack("<HH", 0x100, 0x4000))
        return cpu

    def word(cpu, address):
        return struct.unpack("<H", cpu.mem_read(address, 2))[0]

    # Execute the payload and original native held-key reader together.
    for pressed in ((), (0x11,), (0x1f,), (0x1e,), (0x20,), (0x48,), (0x50,),
                    (0x4b,), (0x4d,), (0x11, 0x1e), (0x1f, 0x20),
                    (0x11, 0x1f, 0x1e, 0x20, 0x4b, 0x4d)):
        for alt, edge in product((False, True), (1, 2, 3)):
            cpu = cpu_for("input")
            reader = found["reader"]
            cpu.mem_write(0x20000, data[reader.start:reader.end])
            cpu.mem_write(0x10000 + profile["retained"]["reader"],
                          struct.pack("<HH", profile["reader_offset"], 0x2000))
            for scan in pressed:
                cpu.mem_write(0x30000 + profile["keys"] + scan, bytes([edge]))
            cpu.mem_write(0x30000 + profile["keys"] + 0x38, bytes([int(alt)]))
            cpu.emu_start(0x10000 + inp + 24, 0x10000 + inp + 0xdb, count=5000)
            mapping = {0x11: 0x10, 0x1f: 0x20, 0x1e: 0x80000, 0x20: 0x100000,
                       0x48: 0x10, 0x50: 0x20, 0x4b: 0x80000 if alt else 4,
                       0x4d: 0x100000 if alt else 8}
            expected = 0
            for scan in pressed:
                if edge & 1:
                    expected |= mapping[scan]
                assert cpu.mem_read(0x30000 + profile["keys"] + scan, 1)[0] == edge & 1
            assert int.from_bytes(cpu.mem_read(0x40128, 4), "little") == expected
            assert cpu.reg_read(UC_X86_REG_SP) == 0x7f00
            count += 1

    # Startup uses the existing init call once; M is never read.
    for available, initial in product((0, 1), (0, 1, 0xffff)):
        cpu = cpu_for("input")
        cpu.mem_write(0x40120, struct.pack("<H", initial))
        calls = []
        def init(uc, address, size, user):
            if address == 0x10000 + inp + 0x285:
                calls.append(True)
                uc.reg_write(UC_X86_REG_AX, available)
                uc.reg_write(UC_X86_REG_IP, inp + 0x28a)
        cpu.hook_add(UC_HOOK_CODE, init)
        for _ in range(2):
            cpu.emu_start(0x10000 + inp + 0x268, 0x10000 + inp + 0x2c1, count=500)
        assert len(calls) == int(initial == 0)
        assert word(cpu, 0x40120) == (initial or (1 if available else 0xffff))
        count += 1

    # Real mouse calls now produce pending degrees and a fractional remainder,
    # without virtual left/right bits. Include the driver's entire signed range.
    mouse_flags = 0xa4ac if profile["language"] == "ru" else 0xa55c
    pending_address = 0x08a2 if profile["language"] == "ru" else 0x09b2
    fraction_address = pending_address + 2

    def trunc_div(value, divisor):
        return (abs(value) // divisor) * (-1 if value < 0 else 1)

    def mouse_sequence(sequence, alt=0, buttons=0):
        cpu = cpu_for("mouse")
        cpu.mem_write(0x30000 + profile["keys"] + 0x38, bytes([alt]))
        sample = [0]
        def interrupt(uc, number, user):
            assert number == 0x33
            fn = uc.reg_read(UC_X86_REG_AX)
            if fn == 5:
                uc.reg_write(UC_X86_REG_AX, buttons)
                uc.reg_write(UC_X86_REG_BX, 0)
            elif fn == 11:
                uc.reg_write(UC_X86_REG_CX, sample[0] & 0xffff)
                uc.reg_write(UC_X86_REG_DX, 32767)  # Y never contributes.
            else:
                raise AssertionError(fn)
        cpu.hook_add(UC_HOOK_INTR, interrupt)
        pending = fraction = 0
        for raw in sequence:
            sample[0] = raw
            cpu.reg_write(UC_X86_REG_BP, 0x8000)
            cpu.reg_write(UC_X86_REG_SP, 0x7f00)
            cpu.mem_write(0x37f00, struct.pack("<HH", 0xffff, 0x1000))
            cpu.emu_start(0x10139, 0x1ffff, count=5000)
            total = raw * 2 + fraction
            degrees = trunc_div(total, 10)
            fraction = total - degrees * 10
            pending += degrees
            pending -= trunc_div(pending, 360) * 360
            actual_pending, actual_fraction = struct.unpack("<hh", cpu.mem_read(0x30000 + pending_address, 4))
            flags = int.from_bytes(cpu.mem_read(0x30000 + mouse_flags, 4), "little")
            expected_flags = (0x200 if buttons & 1 else 0) | (0x810 if buttons & 2 else 0)
            assert flags == expected_flags, (raw, flags, expected_flags)
            assert (actual_pending, actual_fraction) == (pending, fraction)
            assert cpu.reg_read(UC_X86_REG_SP) == 0x7f04
            assert cpu.reg_read(UC_X86_REG_BP) == 0x8000
        return pending, fraction

    for raw, alt, buttons in product((-32768, -8193, -8192, -3, -1, 0, 1, 3, 8192, 32767),
                                     (0, 1), (0, 1, 2, 3)):
        mouse_sequence([raw], alt, buttons)
        count += 1
    for sequence in ([1] * 1000, [-1] * 1000, [31] * 1000, [-31] * 1000,
                     [32767, -32768] * 100, [8192, -1, 0, 1, -8193, 0, 1]):
        mouse_sequence(sequence)
        count += len(sequence)
    # Event partitioning and pauses do not change sensitivity or discard fractions.
    assert mouse_sequence([1] * 10) == mouse_sequence([10]) == (2, 0)
    assert mouse_sequence([1, 0, 1, 0, 1]) == mouse_sequence([3]) == (0, 6)
    assert mouse_sequence([100, -100]) == (0, 0)
    count += 3

    # Mouse and keyboard share these same 4/8 bits and native player code.
    # There is no separate mouse cap: the common angular step caps at 18.
    assert data[found["player"].start + 0xc7e + delta:
                found["player"].start + 0xc89 + delta] == bytes.fromhex("3d12007e0626c7477c1200")

    # Run native player physics through angle update and movement dispatch.
    # Clock/update calls and collision-heavy movement callee are intercepted.
    for forward, strafe, turn, shift in product(range(4), range(4), range(4), (0, 1)):
        for mouse_delta, initial_angle in product((0, -359, -120, -1, 1, 120, 359), (0, 180, 359)):
            flags = ((0, 0x10, 0x20, 0x30)[forward] | (0, 0x80000, 0x100000, 0x180000)[strafe]
                     | (0, 4, 8, 12)[turn] | (0x2000 if shift else 0))
            cpu = cpu_for("player")
            cpu.mem_write(0x37f00, struct.pack("<HH", profile["hero_return"], 0x1000))
            cpu.mem_write(0x37f04, struct.pack("<HH", 0x100, 0x4000))
            cpu.mem_write(0x4011c, struct.pack("<I", flags))
            cpu.mem_write(0x30000 + pending_address, struct.pack("<h", mouse_delta))
            cpu.mem_write(0x40190, struct.pack("<II", 10000, 10000))
            cpu.mem_write(0x40132, struct.pack("<HH", initial_angle, 3))
            cpu.mem_write(0x40138, struct.pack("<I", 400))
            cpu.mem_write(0x40182, struct.pack("<H", 1 if turn == 2 else 0))
            cpu.reg_write(UC_X86_REG_SI, 0x1234)
            cpu.reg_write(UC_X86_REG_DI, 0x5678)
            calls = []
            def intercept(uc, address, size, user):
                offset = address - 0x10000 - delta
                if offset in (0xac5, 0xad7, 0xe6d):
                    uc.reg_write(UC_X86_REG_AX, 416 if offset == 0xad7 else 0)
                    uc.reg_write(UC_X86_REG_DX, 0)
                    uc.reg_write(UC_X86_REG_IP, address - 0x10000 + 5)
                elif offset == 0xeb1:
                    sp = uc.reg_read(UC_X86_REG_SP)
                    calls.append((word(uc, 0x30000 + sp + 10),
                                  struct.unpack("<h", uc.mem_read(0x30000 + sp + 8, 2))[0]))
                    ip = word(uc, 0x30000 + sp)
                    uc.reg_write(UC_X86_REG_SP, sp + 4)
                    uc.reg_write(UC_X86_REG_IP, ip)
            cpu.hook_add(UC_HOOK_CODE, intercept)
            cpu.emu_start(0x10000 + player, 0x10000 + profile["hero_return"], count=10000)
            expected_axes = ([0] if forward in (1, 2) else []) + ([1] if strafe in (1, 2) else [])
            assert [axis for axis, amount in calls] == expected_axes, (hex(flags), calls)
            assert all((amount < 0) == (forward == 1 if axis == 0 else strafe == 1) for axis, amount in calls)
            angle = word(cpu, 0x40132)
            step = (6 if shift else 3) * (1 if turn == 1 else -1 if turn == 2 else 0)
            assert angle == (initial_angle - mouse_delta + step) % 360, (flags, initial_angle, mouse_delta, angle, step)
            assert word(cpu, 0x30000 + pending_address) == 0
            assert cpu.reg_read(UC_X86_REG_SP) == 0x7f04
            assert cpu.reg_read(UC_X86_REG_SI) == 0x1234
            assert cpu.reg_read(UC_X86_REG_DI) == 0x5678
            assert cpu.reg_read(UC_X86_REG_BP) == 0x8000
            count += 1
    # The native physics routine is shared by hero and AI. The old helper
    # consumed pending mouse movement on whichever actor happened to run first.
    # Reproduce it, then run several minutes of synthetic interleaved updates.
    helper_region = profile["regions"][6]
    helper_offset = profile["anchors"]["player"]["offset"] + helper_region["relative"]
    hook = 0x10000 + 0xc3d + delta
    cpu = cpu_for("player")
    cpu.mem_write(0x10000 + helper_offset, bytes.fromhex(helper_region["previous_replacements"][-1]))
    cpu.mem_write(0x38002, struct.pack("<H", profile["ai_return"]))
    cpu.mem_write(0x30000 + pending_address, struct.pack("<h", 12))
    cpu.mem_write(0x40132, struct.pack("<H", 180))
    cpu.emu_start(hook, hook + 3, count=100)
    assert word(cpu, 0x30000 + pending_address) == 0  # old code stole it
    assert word(cpu, 0x40132) == 168
    count += 1
    cpu = cpu_for("player")
    cpu.mem_write(0x40132, struct.pack("<H", 180))
    cpu.mem_write(0x40232, struct.pack("<H", 90))
    expected_angle = 180
    for frame in range(18000):  # six minutes at 50 simulation updates/second
        motion = (1, -1, 12, -12, 359, -359, 0)[frame % 7]
        cpu.mem_write(0x30000 + pending_address, struct.pack("<h", motion))
        cpu.mem_write(0x38006, struct.pack("<HH", 0x200, 0x4000))
        cpu.mem_write(0x38002, struct.pack("<H", profile["ai_return"]))
        for _ in range(frame % 4 + 1):
            cpu.emu_start(hook, hook + 3, count=100)
            assert word(cpu, 0x30000 + pending_address) == motion & 0xffff
            assert word(cpu, 0x40232) == 90
            assert cpu.reg_read(UC_X86_REG_SP) == 0x7f00
        cpu.mem_write(0x38006, struct.pack("<HH", 0x100, 0x4000))
        cpu.mem_write(0x38002, struct.pack("<H", profile["hero_return"]))
        cpu.emu_start(hook, hook + 3, count=100)
        expected_angle = (expected_angle - motion) % 360
        assert word(cpu, 0x40132) == expected_angle
        assert word(cpu, 0x30000 + pending_address) == 0
        cpu.emu_start(hook, hook + 3, count=100)  # no duplicate consumption
        assert word(cpu, 0x40132) == expected_angle
        assert cpu.reg_read(UC_X86_REG_SP) == 0x7f00
        count += 1
    assert path.read_bytes() == original
    return {"language": profile["language"], "scenarios": count,
            "source_sha256": hashlib.sha256(original).hexdigest(),
            "patched_sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    print(json.dumps(verify(parser.parse_args().exe), indent=2))
