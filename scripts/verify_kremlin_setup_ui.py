"""Check DOS setup text/attributes in hidden DOSBox, without game files.

BIOS keyboard-buffer injection exercises the actual menu. PNG previews are
reconstructed from guest text cells, BIOS Latin bitmaps and our Cyrillic rasters;
they are text-mode previews, not captures of the emulator's output shader.
"""
import json
import os
import struct
import subprocess
import zlib

from kremlin_setup_font import glyphs
from profile_kremlin import Api, ROOT
from verify_kremlin_settings_dosbox import bios_key, text, wait_for
from wasdify.service import Service
from wasdify.i18n import messages


def preview(api, lang, path):
    cells = api.get('memory/0xb8000/4000')
    # INT 43h points to the 8x8 font on this BIOS. Locate the VGA ROM's
    # separate 8x16 table using its A bitmap, then verify its space glyph.
    rom = api.get('memory/0xc0000/0x10000')
    signature = bytes.fromhex('00 00 10 38 6c c6 c6 fe c6 c6 c6 c6 00 00 00 00')
    assert rom.count(signature) == 1
    at = rom.index(signature) - ord('A') * 16
    font = list(rom[at:at + 4096])
    assert bytes(font[32*16:33*16]) == b'\0' * 16
    if lang == 'ru':
        for code, glyph in zip(list(range(128, 176)) + list(range(224, 240)), glyphs()):
            font[code * 16:code * 16 + 16] = glyph
    palette = [(0, 0, 0), (0, 0, 170), (0, 170, 0), (0, 170, 170),
               (170, 0, 0), (170, 0, 170), (170, 85, 0), (170, 170, 170),
               (85, 85, 85), (85, 85, 255), (85, 255, 85), (85, 255, 255),
               (255, 85, 85), (255, 85, 255), (255, 255, 85), (255, 255, 255)]
    raw = bytearray()
    for y in range(400):
        raw.append(0)
        for x in range(720):
            at = ((y // 16) * 80 + x // 9) * 2
            code, attr = cells[at:at + 2]
            bit = x % 9 < 8 and font[code * 16 + y % 16] & (128 >> (x % 9))
            raw.extend(palette[attr & 15 if bit else (attr >> 4) & 7])
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', 720, 400, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def trial(lang, work):
    game = work / lang
    game.mkdir()
    Service().export_setup(game, lang)
    port = 8137
    api = Api(port)
    try:
        api.get('dosbox/info')
    except OSError:
        pass
    else:
        raise RuntimeError('UI test port already in use')
    config = work / f'{lang}.conf'
    config.write_text(f'''[sdl]
fullscreen = off
[cpu]
cpu_cycles = 10000
[webserver]
webserver_enabled = on
webserver_bind_address = 127.0.0.1
webserver_port = {port}
[autoexec]
mount -u c
mount c "{game}"
c:
SETUP.EXE
''', encoding='ascii')
    with (work / f'{lang}.log').open('wb') as log:
        process = subprocess.Popen([str(ROOT / 'mqa/dosbox/dosbox.exe'), '-conf', str(config), '-noconsole'],
                                   cwd=ROOT / 'mqa/dosbox', stdout=log, stderr=log,
                                   env=dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy'),
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            title = messages(lang)['dos_title'].replace('ё', 'е').replace('Ё', 'Е')
            wait_for(lambda: title in text(api))
            def check_selection(row):
                raw = api.get('memory/0xb8000/4000')
                for y in range(3, 22):
                    attrs = raw[y * 160 + 5:y * 160 + 157:2]
                    assert attrs == bytes([0x1f if y == row else 7]) * 76, (lang, row, y, attrs)
                assert title in text(api).splitlines()[1]
                assert 'wasdify' not in text(api).lower()
            check_selection(3)
            preview(api, lang, work / f'{lang}-main.png')
            bios_key(api, 0x50)
            check_selection(4)
            bios_key(api, 0x50)
            bios_key(api, 0x1c, 13)
            check_selection(3)
            preview(api, lang, work / f'{lang}-keys.png')
            for row in range(4, 22):
                bios_key(api, 0x50)
                check_selection(row)
            assert ('джойстик' if lang == 'ru' else 'joystick') in text(api).splitlines()[21].lower()
            bios_key(api, 0x4d)
            assert text(api).splitlines()[22].rstrip().endswith('2')
            bios_key(api, 1, 27)
            check_selection(5)
            bios_key(api, 0x50)
            bios_key(api, 0x50)
            bios_key(api, 0x1c, 13)
            wait_for(lambda: (game / 'KREMLIN.INI').exists())
            check_selection(7)
            preview(api, lang, work / f'{lang}-saved.png')
            bios_key(api, 0x1c, 13)
            check_selection(7)
            (work / f'{lang}-screen.txt').write_text(text(api), encoding='utf-8')
        finally:
            (work / f'{lang}-final.txt').write_text(text(api), encoding='utf-8')
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    from datetime import datetime
    work = ROOT / 'build' / ('setup-ui-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    work.mkdir(parents=True)
    for language in ('ru', 'en'):
        trial(language, work)
    print(json.dumps({'status': 'passed', 'previews': str(work)}))
