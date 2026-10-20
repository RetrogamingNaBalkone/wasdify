"""Build/verify original DOS setup and NE payloads; NASM is development-only."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wasdify.patches import kremlin_settings_spec as spec

# Original 5x7 uppercase Cyrillic rasters, doubled vertically in an 8x16 cell.
FONT = (
 "01110 10001 10001 11111 10001 10001 10001", # А
 "11111 10000 10000 11110 10001 10001 11110", # Б
 "11110 10001 10001 11110 10001 10001 11110", # В
 "11111 10000 10000 10000 10000 10000 10000", # Г
 "01110 01010 01010 01010 01010 11111 10001", # Д
 "11111 10000 10000 11110 10000 10000 11111", # Е
 "10101 10101 10101 01110 10101 10101 10101", # Ж
 "01110 10001 00001 00110 00001 10001 01110", # З
 "10001 10001 10011 10101 11001 10001 10001", # И
 "01010 00100 10001 10011 10101 11001 10001", # Й
 "10001 10010 10100 11000 10100 10010 10001", # К
 "00111 01001 01001 01001 01001 01001 10001", # Л
 "10001 11011 10101 10101 10001 10001 10001", # М
 "10001 10001 10001 11111 10001 10001 10001", # Н
 "01110 10001 10001 10001 10001 10001 01110", # О
 "11111 10001 10001 10001 10001 10001 10001", # П
 "11110 10001 10001 11110 10000 10000 10000", # Р
 "01111 10000 10000 10000 10000 10000 01111", # С
 "11111 00100 00100 00100 00100 00100 00100", # Т
 "10001 10001 10001 01111 00001 10001 01110", # У
 "00100 01110 10101 10101 10101 01110 00100", # Ф
 "10001 10001 01010 00100 01010 10001 10001", # Х
 "10010 10010 10010 10010 10010 11111 00001", # Ц
 "10001 10001 10001 01111 00001 00001 00001", # Ч
 "10101 10101 10101 10101 10101 10101 11111", # Ш
 "10101 10101 10101 10101 10101 11111 00001", # Щ
 "11000 01000 01000 01110 01001 01001 01110", # Ъ
 "10001 10001 10001 11101 10011 10011 11101", # Ы
 "10000 10000 10000 11110 10001 10001 11110", # Ь
 "01110 10001 00001 00111 00001 10001 01110", # Э
 "10110 11001 11001 11001 11001 11001 10110", # Ю
 "01111 10001 10001 01111 00101 01001 10001", # Я
)


def db(label, value):
    if isinstance(value, str):
        value = value.encode("cp866") + b"\0"
    return label + ": db " + ",".join(str(b) for b in value) + "\n"


def tables(work):
    constants = {key: value for key, value in vars(spec).items()
                 if key.isupper() and isinstance(value, int)}
    constants = {("CFG_" + k if k not in ("FILE_LIMIT", "LINE_LIMIT") else k): v
                 for k, v in constants.items()}
    constants.update(ACTION_COUNT=len(spec.ACTIONS), SCAN_COUNT=len(spec.SCANCODES))
    (work / "settings-tables.inc").write_text(
        "".join(f"%define {k} {v}\n" for k, v in constants.items()) +
        "%if ENG\n%define KEYBOARD_OBJECT 0x228a\n%define MOUSE_X 0x9b2\n"
        "%define MOUSE_FRACTION 0x9b4\n%define PAUSE_TEXT 0xb61\n"
        "%define MAIN_LOOP_LOCAL -0x48\n%define MAIN_MODE_LOCAL -0x58\n%define MAIN_TYPE_LOCAL -0x5a\n%else\n"
        "%define KEYBOARD_OBJECT 0x21da\n%define MOUSE_X 0x8a2\n"
        "%define MOUSE_FRACTION 0x8a4\n%define PAUSE_TEXT 0xaad\n"
        "%define MAIN_LOOP_LOCAL -0xa\n%define MAIN_MODE_LOCAL -0x1a\n%define MAIN_TYPE_LOCAL -0x1c\n%endif\n", encoding="ascii")
    text = db("default_keys", bytes(x for a in spec.ACTIONS for x in a[1:3]))
    text += db("allowed_scans", bytes(spec.SCANCODES))
    text += db("action_edges", bytes(a[3] for a in spec.ACTIONS))
    text += "action_masks: dd " + ",".join(str(a[4]) for a in spec.ACTIONS) + "\n"
    text += "action_names: dw " + ",".join("key_" + a[0] for a in spec.ACTIONS) + "\n"
    text += "".join(db("key_" + a[0], a[0] + "=") for a in spec.ACTIONS)
    (work / "settings-table-data.inc").write_text(text, encoding="ascii")


def ui(work, lang):
    locale = json.loads((ROOT / f"src/wasdify/locales/{lang}.json").read_text("utf-8"))
    def tr(key):
        return locale["dos_" + key].upper().replace("Ё", "Е").replace("WASDIFY", "WASDify") if lang == "ru" else locale["dos_" + key]
    text = ""
    for key in ("title", "help", "capture", "slot", "saved", "conflict", "write_error", "path_error"):
        text += db("ui_" + key, tr(key))
    for key, val in (("crlf", "\r\n"), ("marker", " > "), ("blank", "   "), ("separator", " / ")):
        text += db("ui_" + key, val)
    main = ("video", "mouse", "keys", "defaults", "save", "exit")
    text += "main_labels: dw " + ",".join("label_"+k for k in main) + "\n"
    text += "".join(db("label_"+k, tr(k)+": ") for k in main)
    text += "action_labels: dw " + ",".join("label_"+a[0] for a in spec.ACTIONS) + "\n"
    text += "".join(db("label_"+a[0], tr(a[0]).ljust(24)) for a in spec.ACTIONS)
    modes = ("auto", "vga", "640x400", "640x480", "800x600", "1024x768")
    text += "mode_labels: dw " + ",".join("mode_"+str(i) for i in range(6)) + "\n"
    text += "".join(db("mode_"+str(i), tr(k) if i < 2 else k) for i, k in enumerate(modes))
    names = {0: tr("unbound"), 1:"Esc",14:"Backspace",15:"Tab",28:"Enter",29:"Ctrl",42:"LShift",
             54:"RShift",55:"Num*",56:"Alt",57:"Space",58:"CapsLock",69:"NumLock",70:"ScrollLock",
             71:"Home/Num7",72:"Up/Num8",73:"PgUp/Num9",74:"Num-",75:"Left/Num4",76:"Num5",
             77:"Right/Num6",78:"Num+",79:"End/Num1",80:"Down/Num2",81:"PgDn/Num3",
             82:"Insert/Num0",83:"Delete/Num.",87:"F11",88:"F12"}
    for start, chars in ((2,"1234567890-="),(16,"QWERTYUIOP[]"),(30,"ASDFGHJKL;'`"),(43,"\\ZXCVBNM,./")):
        names.update({start+i: c for i,c in enumerate(chars)})
    names.update({59+i:"F"+str(i+1) for i in range(10)})
    names.update({i: tr("key_"+str(i)) for i in names if i != 0 and len(names[i]) > 1})
    text += "scan_labels: dw " + ",".join("scan_"+str(i) for i in range(128)) + "\n"
    text += "".join(db("scan_"+str(i), names.get(i,"?")) for i in range(128))
    font = b"".join(b"\0" + b"".join(bytes((int(row,2)<<1,))*2 for row in glyph.split()) + b"\0" for glyph in FONT)
    assert len(font) == 512
    text += db("russian_font", font)
    (work / "settings-ui.inc").write_text(text, encoding="ascii")


def build(nasm, refresh=False):
    work = ROOT / "build/settings"
    work.mkdir(parents=True, exist_ok=True)
    tables(work)
    result = {"format": 1, "data": spec.initial_data().hex(), "runtime": {}, "setup": {}}
    def assemble(source, name, defines):
        path = work / name
        subprocess.run([str(nasm), "-f", "bin", "-I", str(work)+"/", "-I",
                        str(ROOT / "src/wasdify/patches")+"/", *[f"-D{k}={v}" for k,v in defines.items()],
                        str(ROOT / "src/wasdify/patches" / source), "-o", str(path)], check=True)
        return path.read_bytes()
    for lang in ("ru", "en"):
        runtime = assemble("kremlin_settings.asm", f"runtime-{lang}.bin", {"ENG": int(lang=="en")})
        result["runtime"][lang] = runtime.hex()
        ui(work, lang)
        definitions = {"ENG": 0, "LANG_RU": int(lang=="ru"), "DATA_PARAS": 0}
        code = assemble("kremlin_setup.asm", f"setup-{lang}.bin", definitions)
        paragraphs = (len(code)+15)//16
        definitions["DATA_PARAS"] = paragraphs
        code = assemble("kremlin_setup.asm", f"setup-{lang}.bin", definitions)
        assert paragraphs == (len(code)+15)//16
        image = code.ljust(paragraphs*16, b"\0") + spec.initial_data()
        size = 32+len(image)
        header = struct.pack("<14H", 0x5a4d, size%512, (size+511)//512, 0, 2,
                             (65536-spec.DATA_SIZE)//16, 0xffff, paragraphs, 0xfff0,
                             0, struct.unpack_from("<H",code,8)[0], 0, 28, 0).ljust(32,b"\0")
        exe = header+image
        (work/f"SETUP-{lang.upper()}.EXE").write_bytes(exe)
        result["setup"][lang] = exe.hex()
        print(lang, "runtime", len(runtime), hashlib.sha256(runtime).hexdigest(),
              "setup", len(exe), hashlib.sha256(exe).hexdigest())
    target = ROOT / "src/wasdify/data/kremlin_settings.json"
    if refresh:
        target.write_text(json.dumps(result, separators=(",", ":"))+"\n", encoding="ascii")
    elif result != json.loads(target.read_text("ascii")):
        raise ValueError("Packaged settings payloads differ; review before --refresh")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nasm", default="nasm")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    build(args.nasm, args.refresh)
