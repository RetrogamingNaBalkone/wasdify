"""Execute packaged configuration instructions against synthetic bytes (Unicorn)."""
import struct

from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_HOOK_CODE, UC_HOOK_INTR, UC_HOOK_INSN
from unicorn.x86_const import *

from wasdify.patches.kremlin_settings import resources
from wasdify.patches import kremlin_settings_spec as spec


class Machine:
    def __init__(self, code):
        self.u = Uc(UC_ARCH_X86, UC_MODE_16)
        self.u.mem_map(0, 0x100000)
        self.code = code
        self.exports = struct.unpack_from("<8H",code,8)
        self.u.mem_write(0x10000,code)
        self.u.mem_write(0x20000,spec.initial_data())
        self.u.hook_add(UC_HOOK_CODE,self.observe)

    def observe(self,u,address,size,user):
        if address == 0x18000:
            u.emu_stop()

    def call(self,address,**registers):
        regs = dict(CS=0x1000, DS=0x2000, SS=0x3000, ES=0x2000, SP=0xe000, BP=0xd000)
        regs.update(registers)
        for name,value in regs.items():
            self.u.reg_write(globals()["UC_X86_REG_"+name],value)
        self.u.mem_write(0x30000+regs['SP'],struct.pack('<H',0x8000))
        self.u.emu_start(0x10000+address,0x18000,count=2000000)
        assert self.u.reg_read(UC_X86_REG_IP)==0x8000,hex(self.u.reg_read(UC_X86_REG_IP))

    def parse(self,text):
        raw=text.encode('ascii') if isinstance(text,str) else text
        self.u.mem_write(0x20000+spec.BUFFER,raw)
        self.call(self.exports[3],CX=len(raw))
        return (struct.unpack('<hh',self.u.mem_read(0x20000+spec.VIDEO,4)),
                bytes(self.u.mem_read(0x20000+spec.BINDINGS,len(spec.ACTIONS)*2)),
                self.u.mem_read(0x20000+spec.VALID,1)[0])


def parser_checks(code):
    m=Machine(code)
    defaults=spec.initial_data()[spec.BINDINGS:spec.BINDINGS+len(spec.ACTIONS)*2]
    assert m.parse('[wasdify]\r\nversion=1\r\n')==((0,5),defaults,1)
    assert m.parse('[wasdify]\nversion=2\n[mouse]\nsensitivity=9\n')==((0,7),defaults,0)
    for mode in range(-1,5):
        for step in range(10):
            values,keys,valid=m.parse(f'[wasdify]\nversion=1\n[video]\nmode={mode}\n[mouse]\nsensitivity={step}\n')
            assert values==(mode,step) and valid and keys==defaults,(values,mode,step)
    values,keys,valid=m.parse(' [WASDify] \n VERSION = 1 ; comment\n[video]\nmode=4\n[mouse]\nsensitivity=655360\n[keys]\nforward=18,72\n')
    assert values==(4,5) and keys[:2]==bytes((18,72)) and valid
    for bad in ('forward=1,0','forward=256,0','forward=0,89','forward=17,17','forward=garbage'):
        assert m.parse('[wasdify]\nversion=1\n[keys]\n'+bad+'\n')[1]==defaults,bad
    assert m.parse('[wasdify]\nversion=1\n[mouse]\nsensitivity='+'0'*128+'9\n')[0]==(0,5)
    assert m.parse('[wasdify]\nversion=1\n[mouse]\nsensitivity=9\n')[0]==(0,9)
    m.call(m.exports[5])
    length=m.u.reg_read(UC_X86_REG_CX)
    output=bytes(m.u.mem_read(0x20000+spec.OUTPUT,length))
    assert b'version=1\r\n' in output and b'sensitivity=9\r\n' in output
    assert m.parse(output)[0]==(0,9)
    return 70


def runtime_checks(lang,code):
    u=Uc(UC_ARCH_X86,UC_MODE_16)
    u.mem_map(0,0x100000)
    payload=bytearray(code)
    exports=struct.unpack_from('<8H',code,8)
    for i in range(exports[7]):
        source,token=struct.unpack_from('<HH',code,exports[6]+i*4)
        value=struct.pack('<H',0x2000) if token==0 else struct.pack('<HH',token*0x20,0x5000)
        payload[source:source+len(value)]=value
    u.mem_write(0x10000,bytes(payload))
    u.mem_write(0x20000,spec.initial_data())
    keys={}
    callbacks=[]
    object_ptr=0x21da if lang=='ru' else 0x228a
    mouse_x=0x8a2 if lang=='ru' else 0x9b2
    mouse_fraction=mouse_x+2
    stop=[False]
    ini=[None]
    def observe(machine,address,size,user):
        if address==0x18000 or address in (0x50000+9*0x20,0x50000+10*0x20):
            stop[0]=True
            machine.emu_stop()
        elif 0x50000<=address<0x50200:
            token=(address-0x50000)//0x20
            sp=machine.reg_read(UC_X86_REG_SP)
            ip,cs=struct.unpack('<HH',machine.mem_read(0x30000+sp,4))
            callbacks.append(token)
            result=1
            if token in (4,5):
                key=struct.unpack('<H',machine.mem_read(0x30000+sp+8,2))[0]
                state=keys.get(key,0)
                result=state if token==4 else int(state==2)
                keys[key]=state&1
            elif token==1:
                off,sel=struct.unpack('<HH',machine.mem_read(0x30000+sp+4,4))
                assert bytes(machine.mem_read(sel*16+off,20)).startswith(b'C:\\GAME\\KREMLIN.INI\0')
                result=0xffff if ini[0] is None else 3
            elif token==2:
                handle,off,sel,count=struct.unpack('<4H',machine.mem_read(0x30000+sp+4,8))
                assert handle==3 and count==spec.FILE_LIMIT+1
                data=ini[0][:count]
                machine.mem_write(sel*16+off,data)
                result=len(data)
            elif token==3:
                result=0
            machine.reg_write(UC_X86_REG_AX,result)
            machine.reg_write(UC_X86_REG_SP,sp+4)
            machine.reg_write(UC_X86_REG_CS,cs)
            machine.reg_write(UC_X86_REG_IP,ip)
    u.hook_add(UC_HOOK_CODE,observe)
    def run(entry,return_delta=0):
        for name,value in [('CS',0x1000),('DS',0x3000),('SS',0x3000),('ES',0x4000),
                           ('SP',0xe000),('BP',0xd000),('FS',0x6000)]:
            u.reg_write(globals()['UC_X86_REG_'+name],value)
        stop[0]=False
        u.emu_start(0x10000+entry,0xf0000,count=1000000)
        assert stop[0],'payload failed to return'
        assert u.reg_read(UC_X86_REG_SP)==0xe000+return_delta
        assert u.reg_read(UC_X86_REG_DS)==0x3000
        assert u.reg_read(UC_X86_REG_FS)==0x6000
    u.mem_write(0x30000+0xd006,struct.pack('<HH',0x100,0x4000))
    u.mem_write(0x30000+object_ptr,struct.pack('<HH',0x100,0x4000))
    for pressed,expected in [({0x11:1},0x10),({0x20:1},0x100000),
                             ({0x4b:1,0x38:1},0x80000),({0x39:2},0x800),
                             ({0x39:1},0),({0x2a:1,0x1d:1},0x2200)]:
        keys.clear();keys.update(pressed)
        u.mem_write(0x40100+0x28,bytes(4))
        run(exports[1])
        value=struct.unpack('<I',u.mem_read(0x40100+0x28,4))[0]
        assert value==expected,(pressed,hex(value),hex(expected))
    # Rebinding does not continue to respond to W. Physical E drives forward.
    u.mem_write(0x20000+spec.BINDINGS,bytes((0x12,0)))
    keys.clear();keys[0x11]=1
    run(exports[1]);assert struct.unpack('<I',u.mem_read(0x40128,4))[0]==0
    keys.clear();keys[0x12]=1
    run(exports[1]);assert struct.unpack('<I',u.mem_read(0x40128,4))[0]==0x10
    scenarios=8
    for step in range(10):
        u.mem_write(0x20000+spec.SENSITIVITY,struct.pack('<H',step))
        pending=0;remainder=0
        u.mem_write(0x30000+mouse_x,bytes(4))
        denominator=5*(13-step)
        for delta in (1,1,0,-1,-1,-1,0,32767,-32768,10,-10)*30:
            u.mem_write(0x30000+0xcff8,struct.pack('<h',delta))
            numerator=delta*6+remainder
            whole=abs(numerator)//denominator*(1 if numerator>=0 else -1)
            remainder=numerator-whole*denominator
            total=pending+whole
            pending=abs(total)%360*(1 if total>=0 else -1)
            run(exports[2])
            actual=struct.unpack('<hh',u.mem_read(0x30000+mouse_x,4))
            assert actual==(pending,remainder),(lang,step,delta,actual,(pending,remainder))
            scenarios+=1
    bios=[]
    vbe_ok=[True]
    def interrupt(machine,number,user):
        assert number==0x10
        bios.append(machine.reg_read(UC_X86_REG_BX))
        machine.reg_write(UC_X86_REG_AX,0x004f if vbe_ok[0] else 0x014f)
    u.hook_add(UC_HOOK_INTR,interrupt)
    for contents,override,available,expected in (
        (None,False,True,(0,7,0,0)),
        (b'[wasdify]\nversion=2\n',False,True,(0,7,0,0)),
        (b'[wasdify]\nversion=1\n[video]\nmode=4\n[mouse]\nsensitivity=9\n',False,True,(4,9,1,4)),
        (b'[wasdify]\nversion=1\n[video]\nmode=4\n',False,False,(4,5,1,0)),
        (b'[wasdify]\nversion=1\n[video]\nmode=4\n',True,True,(4,5,1,0)),
        (b'x'*4097,False,True,(0,7,0,0)),
    ):
        ini[0]=contents;vbe_ok[0]=available;bios.clear()
        u.mem_write(0x3e000,struct.pack('<HH',0x8000,0x1000))
        u.mem_write(0x3d006,struct.pack('<HHH',2 if override else 1,0x1000,0x3000))
        u.mem_write(0x31000,struct.pack('<4H',0x1100,0x3000,0x1200,0x3000))
        u.mem_write(0x31100,b'C:\\GAME\\PKR.EXE\0')
        u.mem_write(0x31200,b'-r2\0')
        mode_local=-0x1a if lang=='ru' else -0x58
        u.mem_write(0x3d000+mode_local,bytes(2))
        run(exports[0],4)
        video,sens=struct.unpack('<hh',u.mem_read(0x20000+spec.VIDEO,4))
        valid=u.mem_read(0x20000+spec.VALID,1)[0]
        local=struct.unpack('<h',u.mem_read(0x3d000+mode_local,2))[0]
        assert (video,sens,valid,local)==expected,(lang,expected,(video,sens,valid,local))
        if override:assert not bios
        scenarios+=1
    return scenarios


def save_checks(code):
    cases=('new','existing','existing_backup','short_write','read_error','verify_error',
           'close_error','backup_error','replace_error','rollback_error','temp_exists')
    for scenario in cases:
        m=Machine(code)
        m.parse('[wasdify]\nversion=1\n[mouse]\nsensitivity=3\n')
        u=m.u
        filename='C:\\GAME\\KREMLIN.INI'
        temporary='C:\\GAME\\KREMLIN.TMP'
        backup='C:\\GAME\\KRE0000.BAK'
        old=b'original configuration\r\n'
        files={} if scenario=='new' else {filename:old}
        if scenario=='existing_backup':files[backup]=b'older backup'
        if scenario=='temp_exists':files[temporary]=b'other process temporary'
        handles={};next_handle=[5]
        u.mem_write(0x20000+spec.PATH,filename.encode()+b'\0')
        def string(segment,offset):
            raw=bytes(u.mem_read(segment*16+offset,272))
            return raw.split(b'\0',1)[0].decode('ascii')
        def interrupt(machine,number,user):
            assert number==0x21
            ax=machine.reg_read(UC_X86_REG_AX);ah=ax>>8
            ds=machine.reg_read(UC_X86_REG_DS);dx=machine.reg_read(UC_X86_REG_DX)
            bx=machine.reg_read(UC_X86_REG_BX);cx=machine.reg_read(UC_X86_REG_CX)
            path=string(ds,dx) if ah in (0x5b,0x3d,0x43,0x41,0x56) else None
            error=0;result=0
            if ah==0x5b:
                if path in files:error=80
                else:
                    files[path]=b'';result=next_handle[0];next_handle[0]+=1;handles[result]=[path,0]
            elif ah==0x3d:
                if path not in files:error=2
                else:result=next_handle[0];next_handle[0]+=1;handles[result]=[path,0]
            elif ah==0x40:
                data=bytes(machine.mem_read(ds*16+dx,cx))
                if scenario=='short_write':data=data[:-1]
                files[handles[bx][0]]=data;result=len(data)
            elif ah==0x3e:
                del handles[bx]
                if scenario=='close_error':error=5
            elif ah==0x3f:
                if scenario=='read_error':error=5
                else:
                    data=files[handles[bx][0]][:cx]
                    if scenario=='verify_error':data=bytes((data[0]^1,))+data[1:]
                    machine.mem_write(ds*16+dx,data);result=len(data)
            elif ah==0x43:
                if path not in files:error=2
            elif ah==0x41:
                if path in files:del files[path]
                else:error=2
            elif ah==0x56:
                target=string(machine.reg_read(UC_X86_REG_ES),machine.reg_read(UC_X86_REG_DI))
                if (target in files or path not in files
                    or (scenario=='backup_error' and path==filename)
                    or (scenario in ('replace_error','rollback_error') and path==temporary)
                    or (scenario=='rollback_error' and path==backup)):
                    error=5
                else:files[target]=files.pop(path)
            else:raise AssertionError(hex(ax))
            flags=machine.reg_read(UC_X86_REG_EFLAGS)
            machine.reg_write(UC_X86_REG_EFLAGS,flags|1 if error else flags&~1)
            machine.reg_write(UC_X86_REG_AX,error or result)
        u.hook_add(UC_HOOK_INTR,interrupt)
        m.call(m.exports[6])
        failed=bool(u.reg_read(UC_X86_REG_EFLAGS)&1)
        if scenario in ('new','existing','existing_backup'):
            assert not failed,scenario
            assert b'sensitivity=3\r\n' in files[filename]
            if scenario!='new':assert old in (files.get(backup),files.get('C:\\GAME\\KRE0001.BAK'))
        else:
            assert failed,scenario
            assert old in files.values(),(scenario,files)
        assert not handles,(scenario,handles)
    return len(cases)


def irq_checks(code):
    cases=(([0x11],0x11),([0x2a],0x2a),([0x1d],0x1d),([0xe0,0x1d],0x1d),
           ([0xe0,0x2a,0xe0,0x37],0),([0xe1,0x1d,0x45,0xe1,0x9d,0xc5],0),
           ([0x91],0),([0xe0,0x48],0x48))
    for packets,expected in cases:
        m=Machine(code);u=m.u;current=[0];outputs=[]
        def port_in(machine,port,size,user):
            assert port in (0x60,0x61)
            return current[0] if port==0x60 else 0
        def port_out(machine,port,size,value,user):
            outputs.append((port,value))
        u.hook_add(UC_HOOK_INSN,port_in,None,1,0,UC_X86_INS_IN)
        u.hook_add(UC_HOOK_INSN,port_out,None,1,0,UC_X86_INS_OUT)
        for byte in packets:
            current[0]=byte;outputs.clear()
            for name,value in [('CS',0x1000),('DS',0x2000),('SS',0x3000),('SP',0xe000),('EAX',0xaabbccdd)]:
                u.reg_write(globals()['UC_X86_REG_'+name],value)
            u.mem_write(0x3e000,struct.pack('<3H',0x8000,0x1000,0x202))
            u.emu_start(0x10000+m.exports[1],0x18000,count=1000)
            assert u.reg_read(UC_X86_REG_SP)==0xe006
            assert u.reg_read(UC_X86_REG_EAX)==0xaabbccdd
            assert outputs==[(0x61,0x80),(0x61,0),(0x20,0x20)],outputs
        assert u.mem_read(0x10000+m.exports[2],1)[0]==expected,(packets,expected)
    return len(cases)


def main():
    result=resources();total=0
    for lang in ('ru','en'):
        code=bytes.fromhex(result['runtime'][lang])
        total+=parser_checks(code)+runtime_checks(lang,code)
        from wasdify.service import Service
        from wasdify.resources import read_resource
        declaration = next(f for p in Service().list_patches('dungeons-of-kremlin')
                           if p.id == 'game-settings' for f in p.files if f.language == lang)
        exe=read_resource(declaration.source, declaration.sha256);paras=struct.unpack_from('<H',exe,8)[0]
        image=exe[paras*16:]
        code_size=struct.unpack_from('<H',exe,14)[0]*16
        total+=parser_checks(image[:code_size])
        total+=save_checks(image[:code_size])
        total+=irq_checks(image[:code_size])
    print(f'{total} configuration/parser/input/mouse instruction scenarios passed')


if __name__=='__main__':
    main()
