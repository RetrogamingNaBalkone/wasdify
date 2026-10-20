"""Execute weapon-selection instructions in Unicorn (development only).

Read-only inputs; original files never become fixtures or outputs.
"""
import argparse
import hashlib
from pathlib import Path
import struct

from unicorn import Uc, UcError, UC_ARCH_X86, UC_MODE_16, UC_HOOK_CODE
from unicorn.x86_const import (
    UC_X86_REG_CS, UC_X86_REG_SS, UC_X86_REG_DS, UC_X86_REG_SP,
    UC_X86_REG_BP, UC_X86_REG_ES, UC_X86_REG_IP,
    UC_X86_REG_BX,
)
from wasdify.engine import build_result
from wasdify.patches.kremlin_weapons import (
    WeaponSelectionFix, BEGIN, TIMER_BEGIN, TIMER_END, TIMER_REPLACEMENT,
    timer_contexts,
)


def check_timer_insertion():
    """Run native ring insertion, including insertion from inside a callback."""
    for context, cursor in zip(timer_contexts(), (0x3ec, 0x3f4)):
        for patched in (False, True):
            for empty in (False, True):
                u = Uc(UC_ARCH_X86, UC_MODE_16)
                for base in (0x10000, 0x20000, 0x30000, 0x40000):
                    u.mem_map(base, 0x10000)
                for reg, value in ((UC_X86_REG_CS, 0x1000), (UC_X86_REG_DS, 0x2000),
                                   (UC_X86_REG_SS, 0x3000), (UC_X86_REG_BP, 0x8000),
                                   (UC_X86_REG_SP, 0x7000), (UC_X86_REG_ES, 0x4000),
                                   (UC_X86_REG_BX, 0x200)):
                    u.reg_write(reg, value)
                old = struct.pack('<HH', 0x100, 0x4000)
                new = struct.pack('<HH', 0x200, 0x4000)
                u.mem_write(0x20000+cursor-4, struct.pack('<H', 0 if empty else 1))
                u.mem_write(0x20000+cursor, old)
                u.mem_write(0x40108, old+old)
                u.mem_write(0x38006, new)
                code = bytearray(context)
                if patched:
                    code[TIMER_BEGIN:TIMER_END] = TIMER_REPLACEMENT
                u.mem_write(0x11000, bytes(code))
                u.emu_start(0x11000, 0x11000+len(code), count=1000)
                # Original insertion moves the active dispatch cursor to the new
                # object. A callback then makes the dispatcher use the wrong count.
                expected = new if empty or not patched else old
                assert bytes(u.mem_read(0x20000+cursor, 4)) == expected
                assert bytes(u.mem_read(0x40208, 8)) == (new+new if empty else old+old)
                if not empty:
                    assert bytes(u.mem_read(0x40108, 8)) == new+new
                assert struct.unpack('<H', u.mem_read(0x20000+cursor-4, 2))[0] == (1 if empty else 2)
    return 8


def execute(code, current, requested, owned, invalid=False):
    u = Uc(UC_ARCH_X86, UC_MODE_16)
    for base in (0x10000, 0x20000, 0x30000, 0x40000):
        u.mem_map(base, 0x10000)
    for reg, value in ((UC_X86_REG_CS,0x1000),(UC_X86_REG_SS,0x3000),
                       (UC_X86_REG_DS,0x4000),(UC_X86_REG_SP,0xf000),
                       (UC_X86_REG_BP,0xe000),(UC_X86_REG_ES,0x2000)):
        u.reg_write(reg,value)
    u.mem_write(0x3f000, struct.pack('<5H',0x8000,0x1000,0x100,0x2000,requested))
    u.mem_write(0x201ce,struct.pack('<HH',current,99))
    for slot in owned:
        pointer = (0,0x5000) if invalid and slot==current else (0x100*slot,0x4000)
        u.mem_write(0x201aa+4*slot,struct.pack('<HH',*pointer))
        if pointer[1]==0x4000:
            u.mem_write(0x40000+pointer[0]+0x81,struct.pack('<HH',slot,4))
    outcome=[]
    # Place the function at offset 1000h, preserving relative branches.
    u.mem_write(0x11000,code)
    # Correct linear addresses of the two branch targets.
    def paths(machine,address,size,_):
        if address in (0x11087,0x110c7,0x18000):
            outcome.append({0x11087:'hide',0x110c7:'activate',0x18000:'return'}[address])
            machine.emu_stop()
    u.hook_add(UC_HOOK_CODE,paths)
    try:
        u.emu_start(0x11000,0x18001,count=1000)
    except UcError:
        return 'fault',None
    assert outcome,hex(u.reg_read(UC_X86_REG_IP))
    if outcome[0]=='return':
        assert u.reg_read(UC_X86_REG_SP)==0xf004
        assert u.reg_read(UC_X86_REG_BP)==0xe000
        assert u.reg_read(UC_X86_REG_DS)==0x4000
    return outcome[0],struct.unpack('<H',u.mem_read(0x201d0,2))[0]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path)
    args=parser.parse_args()
    data=args.exe.read_bytes()
    analysis=WeaponSelectionFix().analyze({'main':data})
    change=analysis.changes[0]
    entry=change.offset-BEGIN
    patched=build_result({'main':data},analysis.changes)['main']
    original_code=data[entry:entry+0xf2]
    patched_code=patched[entry:entry+0xf2]
    count=0
    # A null or stale pointer must not be read just to reselect a slot.
    for current,owned,invalid in ((0,(),False),(2,(2,),True)):
        assert execute(original_code,current,current,owned,invalid)[0]=='fault'
        assert execute(patched_code,current,current,owned,invalid)==('return',current)
        count+=1
    for current in (1,2,3):
        for requested in (1,2,3):
            for owned in ((current,), (1,2,3)):
                before=execute(original_code,current,requested,owned)
                after=execute(patched_code,current,requested,owned)
                assert before==after,(current,requested,owned,before,after)
                count+=1
    assert execute(patched_code,0,2,(2,))==('activate',2)
    print({'source_sha256':hashlib.sha256(data).hexdigest(),
           'patched_sha256':hashlib.sha256(patched).hexdigest(),'scenarios':count+1,
           'timer_insertion_scenarios':check_timer_insertion()})


if __name__=='__main__':
    main()
