"""Isolated hidden DOSBox smoke tests on new mqa/ copies; never edits references."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time
import urllib.error

from profile_kremlin import Api, ROOT
from wasdify.service import Service
from wasdify.patches.kremlin_settings_spec import BINDINGS, SENSITIVITY, VIDEO, VALID, STATES


def wait_for(operation, timeout=25):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            value=operation()
            if value:return value
        except (OSError,ValueError):
            pass
        time.sleep(.1)
    raise TimeoutError('Guest did not reach the expected state')


def text(api):
    raw=api.get('memory/0xb8000/4000')
    return '\n'.join(raw[y*160:y*160+160:2].decode('cp866') for y in range(25))


def bios_key(api,scan,char=0):
    head,tail=struct.unpack('<HH',api.get('memory/0x41a/4'))
    next_tail=0x1e if tail+2>=0x3e else tail+2
    if next_tail==head:raise RuntimeError('BIOS ring is full')
    api.put(str(0x400+tail),bytes((char,scan)))
    api.put(str(0x41c),struct.pack('<H',next_tail))
    time.sleep(.15)


def trial(language, mode, fresh_setup=False, machine='svga_s3', argument='', sensitivity=5,
          gameplay=False, ini_kind='valid', no_mouse=False):
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    work=ROOT/'mqa'/('settings-'+stamp)
    game=work/('RUS' if language=='ru' else 'ENG')
    game.parent.mkdir(parents=True)
    shutil.copytree(ROOT/'originals/games/pkr'/game.name,game)
    exe=game/('PKR.EXE' if language=='ru' else 'PKE.EXE')
    service=Service()
    patches=tuple(p.id for p in service.list_patches('dungeons-of-kremlin'))
    plan=service.plan({'main':exe},patches)
    if not plan.ready:raise RuntimeError(plan.issues)
    service.apply(plan)
    if not fresh_setup:
        (game/'KREMLIN.INI').write_bytes(
            f'[wasdify]\r\nversion=1\r\n[video]\r\nmode={mode}\r\n[mouse]\r\nsensitivity={sensitivity}\r\n[keys]\r\nforward=18,72\r\n'.encode('ascii'))
        if ini_kind=='missing':(game/'KREMLIN.INI').unlink()
        elif ini_kind=='invalid':(game/'KREMLIN.INI').write_bytes(b'[wasdify]\r\nversion=2\r\n')
        elif ini_kind=='oversize':(game/'KREMLIN.INI').write_bytes(b'x'*4097)
    port=8127
    api=Api(port)
    try:api.get('dosbox/info')
    except OSError:pass
    else:raise RuntimeError('Test port already in use')
    setup=f'{game.name}\\SETUP.EXE\n' if fresh_setup else ''
    config=f'''[sdl]
fullscreen = off
[dosbox]
machine = {machine}
memsize = 32
[cpu]
core = normal
cputype = pentium
cpu_cycles = 60000
cpu_cycles_protected = 60000
[mouse]
builtin_dos_mouse_driver = {'off' if no_mouse else 'on'}
[webserver]
webserver_enabled = on
webserver_bind_address = 127.0.0.1
webserver_port = {port}
[autoexec]
mount -u c
mount c "{work}"
c:
{setup}cd {game.name}
{exe.name} {argument}
'''
    cfg=work/'dosbox.conf';cfg.write_text(config,encoding='ascii')
    env=dict(os.environ,SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy')
    with (work/'stdout.log').open('wb') as out,(work/'stderr.log').open('wb') as err:
        process=subprocess.Popen([str(ROOT/'mqa/dosbox/dosbox.exe'),'-conf',str(cfg),'-noconsole'],
                                 cwd=ROOT/'mqa/dosbox',env=env,stdout=out,stderr=err,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_for(lambda:api.get('dosbox/info'))
            report={'language':language,'mode':mode,'machine':machine,'argument':argument,
                    'directory':str(work),'ini_kind':ini_kind,'no_mouse':no_mouse}
            if fresh_setup:
                wait_for(lambda:'WASDIFY' in text(api).upper())
                report['setup_text']=text(api)
                for _ in range(4):bios_key(api,0x50)
                bios_key(api,0x1c,13)
                wait_for(lambda:(game/'KREMLIN.INI').exists())
                report['ini']=(game/'KREMLIN.INI').read_text('ascii')
                bios_key(api,0x1c,13) # acknowledge saved message
                bios_key(api,0x1c,13) # save again, exercise unique backup
                wait_for(lambda:(game/'KRE0000.BAK').exists())
                assert (game/'KRE0000.BAK').read_bytes()==(game/'KREMLIN.INI').read_bytes()
                bios_key(api,0x1c,13)
                bios_key(api,1,27)   # exit setup
            def configuration():
                memory=api.get('memory/0/0x2000000')
                at=memory.find(b'WASDify-CFG-v1\0\0')
                while at>=0:
                    if memory[at+VALID]==(1 if ini_kind=='valid' else 0):
                        return at,memory
                    at=memory.find(b'WASDify-CFG-v1\0\0',at+1)
            found,memory=wait_for(configuration)
            report['config_base']=found
            report['settings']=struct.unpack_from('<hh',memory,found+VIDEO)
            report['forward_keys']=list(memory[found+BINDINGS:found+BINDINGS+2])
            # The native DS image has a unique small diagnostic anchor.
            anchor=b'OVERFLOW\0UNDERFLOW\0TLOSS\0PLOSS'
            at=memory.find(anchor)
            ds=at-(0x1082 if language=='ru' else 0x1132)
            report['game_data']=ds
            def geometry():
                value=struct.unpack('<HH',api.get(f'memory/{ds+(0xc1fc if language=="ru" else 0xc2ac)}/4'))
                return value if value[0] in (320,640,800,1024) and value[1] in (200,400,480,600,768) else None
            report['geometry']=wait_for(geometry)
            effective=int(argument[2:]) if argument.startswith('-r') and argument[2:].isdigit() else mode
            if ini_kind!='valid' and not argument:effective=0
            expected={0:(320,200),1:(640,400),2:(640,480),3:(800,600),4:(1024,768)}
            if machine=='vgaonly' and not argument:effective=0
            if effective in expected:
                assert report['geometry']==expected[effective],report
            assert report['settings']==((mode,sensitivity) if ini_kind=='valid' else (0,7)),report
            assert report['forward_keys']==([17,72] if fresh_setup or ini_kind!='valid' else [18,72]),report
            if gameplay:
                key_base=0x2155 if language=='ru' else 0x2205
                def game_key(scan,held=False):
                    pending=struct.unpack('<H',api.get(f'memory/{ds+key_base-3}/2'))[0]
                    api.put(str(ds+key_base+scan),b'\x03')
                    api.put(str(ds+key_base-3),struct.pack('<HB',pending+1,scan))
                    if not held:
                        time.sleep(.12)
                        api.put(str(ds+key_base+scan),b'\x02')
                for _ in range(8):
                    time.sleep(.7)
                    game_key(0x1c)
                # Poll observation: rebinding E replaces W, while D is solely
                # strafe. Values come from the installed runtime's action table.
                checks=[]
                for scan,action,expected in ((0x12,0,1),(0x11,0,0),(0x20,3,1)):
                    game_key(scan,True)
                    time.sleep(.4)
                    actual=api.get(f'memory/{found+STATES+action}/1')[0]
                    checks.append([scan,action,actual])
                    api.put(str(ds+key_base+scan),b'\x00')
                    assert actual==expected,checks
                report['gameplay_keys']=checks
            report['cpu']=json.loads(api.get('cpu/state'))
            (work/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=True))
        except Exception:
            (work/'failure-screen.txt').write_text(text(api),encoding='utf-8')
            print('Failure artifacts:',work)
            raise
        finally:
            process.terminate();process.wait(timeout=10)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('language',choices=('ru','en'))
    parser.add_argument('--mode',type=int,default=0)
    parser.add_argument('--setup',action='store_true')
    parser.add_argument('--machine',default='svga_s3')
    parser.add_argument('--argument',default='')
    parser.add_argument('--sensitivity',type=int,default=5)
    parser.add_argument('--gameplay',action='store_true')
    parser.add_argument('--ini',choices=('valid','missing','invalid','oversize'),default='valid')
    parser.add_argument('--no-mouse',action='store_true')
    args=parser.parse_args()
    trial(args.language,args.mode,args.setup,args.machine,args.argument,args.sensitivity,args.gameplay,args.ini,args.no_mouse)
