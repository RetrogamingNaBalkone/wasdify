"""Development-only RUS profiling copies and DOSBox HTTP measurements (Python 3.11).

Generated game binaries and full captures stay in ignored mqa/. Requires NASM
only when preparing instrumentation. Never changes the input executable.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = "12f78afc0eba05f76b5dc0bb99da38d7daa20585024c0ba958fe828aa3864d28"
PATCHED = "7acaca975e2c189d41fd9a41153721892f442c075ae058d7475b552e1e564e9a"
MAGIC = b"WASDPROF-v1\0\0\0\0\0"
DATA_ANCHOR = b"OVERFLOW\0UNDERFLOW\0TLOSS\0PLOSS"
FUNCTIONS = [(37, 0x0b12, 0x1074, "horizontal"),
             (37, 0x1074, 0x15c3, "vertical"),
             (37, 0x08dd, 0x0a0b, "present_region"),
             (37, 0x0a0b, 0x0a36, "present_full"),
             (40, 0x086a, 0x0932, "copy_full"),
             (40, 0x0932, 0x0a9c, "copy_region"),
             (40, 0x047d, 0x04d0, "bank")]


def observer_asm(stub_offset, site, counter, entering):
    action = (f"inc dword [{counter}]\nrdtsc\nmov [{counter+4}],eax"
              if entering else
              f"rdtsc\nsub eax,[{counter+4}]\n"
              f"add [{counter+8}],eax\nadc dword [{counter+12}],0\n"
              f"inc dword [{counter+16}]")
    tail = f"mov ax,ss\nnop\njmp near {site+3}" if entering else "pop bp\ndec bp\nretf"
    return (f"BITS 16\nORG {stub_offset}\npushfd\npush eax\npush edx\npush ds\n"
            f"mov ax,0xffff\nmov ds,ax\n{action}\n"
            f"pop ds\npop edx\npop eax\npopfd\n{tail}\n")


def prepare(source, destination, nasm, frame_only=False):
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest not in (ORIGINAL, PATCHED):
        raise ValueError("Only the two researched RUS byte images are supported")
    if destination.exists():
        raise ValueError("Destination must be a new file")
    destination.resolve().relative_to((ROOT / "mqa").resolve())
    ne = struct.unpack_from("<I", raw, 60)[0]
    table = ne + struct.unpack_from("<H", raw, ne + 34)[0]
    shift = struct.unpack_from("<H", raw, ne + 50)[0]
    result = bytearray(raw)
    manifest = {"source_sha256": digest, "functions": [], "clock": "DOSBox guest TSC",
                "frame_only": frame_only}
    for index in (37, 40, 48):
        entry = table + (index - 1) * 8
        sector, length, flags, minimum = struct.unpack_from("<4H", raw, entry)
        start = sector << shift
        body = bytearray(raw[start:start + length])
        old_records = []
        if flags & 0x100:
            count = struct.unpack_from("<H", raw, start + length)[0]
            old_records = [raw[start + length + 2 + i*8:start + length + 10 + i*8]
                           for i in range(count)]
        records = list(old_records)
        if index == 48:
            if length != 1 or minimum != 1 or old_records:
                raise ValueError("Unexpected profiling data segment context")
            body.extend(bytes(16-len(body)))
            body.extend(MAGIC)
            body.extend(bytes(24 * len(FUNCTIONS)))
        else:
            for slot, (seg, begin, end, name) in enumerate(FUNCTIONS):
                if seg != index:
                    continue
                if frame_only and name not in ("present_region", "present_full"):
                    continue
                if body[begin:begin+10] != bytes.fromhex("8c d0 90 45 55 8b ec 1e 8e d8"):
                    raise ValueError(f"Unexpected function prologue: {name}")
                # Only known POP BP / DEC BP / RETF exits; reject every other RETF.
                # Publishable Ghidra listing supplies instruction boundaries.
                listing = (ROOT / "originals/source/pkr-ghidra/export/listing.txt").read_text()
                prefix = f"{0x1000+(seg-1)*8:04x}:"
                exits = [int(line[5:9], 16) for line in listing.splitlines()
                         if line.startswith(prefix) and line.endswith(" RETF")
                         and begin <= int(line[5:9], 16) < end]
                if not exits or any(body[e-2:e+1] != b"\x5d\x4d\xcb" for e in exits):
                    raise ValueError(f"Unexpected return shape: {name}")
                counter = 32 + slot * 24
                for site, entering in [(begin, True)] + [(e-2, False) for e in exits]:
                    stub_offset = len(body)
                    # Incoming full flags/registers are preserved. Original 3 bytes
                    # execute after the observer, including the original RETF.
                    asm = observer_asm(stub_offset, site, counter, entering)
                    work = destination.parent
                    work.mkdir(parents=True, exist_ok=True)
                    asm_path = work / f"profile-{seg}-{site:x}.asm"
                    bin_path = asm_path.with_suffix(".bin")
                    asm_path.write_text(asm, encoding="ascii")
                    subprocess.run([str(nasm), "-f", "bin", str(asm_path), "-o", str(bin_path)], check=True)
                    stub = bin_path.read_bytes()
                    operand = stub_offset + 8  # PUSHFD/EAX/EDX/DS = 7, MOV AX opcode = 1.
                    if stub[7:10] != b"\xb8\xff\xff":
                        raise ValueError("Unexpected assembler encoding")
                    # Non-additive internal selector fixup to data segment 48.
                    records.append(struct.pack("<BBHHH", 2, 0, operand, 48, 0))
                    # No original fixup chain may overlap a detour.
                    for rec in old_records:
                        kind, rflags, loc, _, _ = struct.unpack("<BBHHH", rec)
                        seen = set()
                        while loc != 0xffff:
                            if loc in seen or loc + 2 > length:
                                raise ValueError("Invalid original relocation chain")
                            seen.add(loc)
                            size = 4 if kind in (3, 13) else 2
                            if loc < site+3 and site < loc+size:
                                raise ValueError("Relocation overlaps detour")
                            if rflags & 4:
                                break
                            loc = struct.unpack_from("<H", raw, start+loc)[0]
                    body[site:site+3] = b"\xe9" + struct.pack("<H", (stub_offset-site-3) & 0xffff)
                    body.extend(stub)
                manifest["functions"].append({"name": name, "segment": seg, "entry": begin,
                                               "returns": exits, "counter_offset": counter})
        if len(body) >= 65536:
            raise ValueError("Instrumented segment exceeds 16-bit limit")
        result.extend(bytes((-len(result)) % (1 << shift)))
        new_start = len(result)
        struct.pack_into("<4H", result, entry, new_start >> shift, len(body), flags, len(body))
        result.extend(body)
        if flags & 0x100:
            result.extend(struct.pack("<H", len(records)))
            result.extend(b"".join(records))
    destination.write_bytes(result)
    manifest["instrumented_sha256"] = hashlib.sha256(result).hexdigest()
    destination.with_suffix(".profile.json").write_text(json.dumps(manifest, indent=2))
    return manifest


class Api:
    def __init__(self, port=8096):
        self.url = f"http://127.0.0.1:{port}/api/v1/"
        self.game_base = None

    def get(self, path):
        if path.startswith("memory/SS/") and self.game_base is not None:
            _, _, offset, length = path.split("/")
            path = f"memory/{self.game_base+int(offset, 0)}/{length}"
        with urllib.request.urlopen(self.url + path, timeout=10) as response:
            return response.read()

    def put(self, path, data, expected=None):
        import base64
        if path.startswith("SS/") and self.game_base is not None:
            path = str(self.game_base+int(path.split("/")[1], 0))
        headers = {"Content-Type": "application/octet-stream"}
        if expected is not None:
            headers["If-Match"] = base64.b64encode(expected).decode()
        request = urllib.request.Request(self.url + "memory/" + path, data=data,
                                         method="PUT", headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read()


def runtime(api):
    """Resolve protected-mode selectors through a validated live LDT snapshot.

    DOSBox numeric /memory/segment uses real-mode segment*16, so guest selectors
    must NOT be passed as numeric segments to that endpoint.
    """
    memory = api.get("memory/0/0x2000000")
    # Interrupts can temporarily switch SS between separate HTTP requests.
    # Locate the known data image independently of the currently active SS.
    if memory.count(DATA_ANCHOR) != 1 or memory.count(MAGIC) != 1:
        raise ValueError("Ambiguous or absent game/profiler data image")
    ss_base = memory.find(DATA_ANCHOR) - 0x1082
    magic = memory.find(MAGIC)

    def base(desc):
        if len(desc) != 8:
            return -1
        return int.from_bytes(desc[2:4], "little") | (desc[4] << 16) | (desc[7] << 24)

    candidates = set()
    # Locate an actual present LDT system descriptor (type 2) in the GDT,
    # then validate that its table contains BOTH independently found images.
    for access in (0x82, 0xa2, 0xc2, 0xe2):
        pos = 0
        while True:
            pos = memory.find(bytes([access]), pos)
            if pos < 0:
                break
            desc = memory[max(0, pos-5):pos+3]
            pos += 1
            if len(desc) != 8 or desc[6] & 0x80:
                continue
            limit = int.from_bytes(desc[:2], "little") | ((desc[6] & 15) << 16)
            table = base(desc)
            if not (0x100 <= limit < 65536 and 0 < table < len(memory)-limit):
                continue
            bases = {base(memory[at:at+8]) for at in range(table, table+limit-6, 8)}
            if ss_base in bases and magic-16 in bases:
                candidates.add(table)
    if len(candidates) != 1:
        raise ValueError("Cannot identify a unique LDT")
    table = candidates.pop()
    offset, selector = struct.unpack_from("<HH", memory, ss_base+0xb53a)
    at = table + (selector & ~7)
    buffer_base = base(memory[at:at+8]) + offset
    api.game_base = ss_base
    return {"ldt_base": table, "ss_base": ss_base, "buffer_base": buffer_base,
            "counter_base": magic + 16}


def counters(api, address):
    data = api.get(f"memory/{address}/{24*len(FUNCTIONS)}")
    result = {}
    for i, (_, _, _, name) in enumerate(FUNCTIONS):
        calls, start, lo, hi, completed, _ = struct.unpack_from("<6I", data, i*24)
        result[name] = {"calls": calls, "ticks": lo | (hi << 32), "completed": completed,
                        "last_start": start}
    return result


def key(api, scan, held=False):
    """Reproduce the researched IRQ's state, pending count and last scancode."""
    import time
    pending = struct.unpack("<H", api.get("memory/SS/0x2152/2"))[0]
    api.put(f"SS/{0x2155+scan}", b"\x03")
    api.put("SS/0x2152", struct.pack("<HB", pending+1, scan))
    if not held:
        time.sleep(.12)
        state = api.get(f"memory/SS/{0x2155+scan}/1")[0]
        api.put(f"SS/{0x2155+scan}", bytes([state & ~1]))


def capture(api, mapping, destination, stable=False):
    import time
    width, height = struct.unpack("<HH", api.get("memory/SS/0xc1fc/4"))
    pixels = api.get(f"memory/{mapping['buffer_base']}/{width*height}")
    if stable:
        # An atomic memory read may still occur halfway through a game frame.
        # Require three identical buffers while the player is stationary.
        matches = 0
        for _ in range(60):
            time.sleep(.05)
            next_pixels = api.get(f"memory/{mapping['buffer_base']}/{width*height}")
            matches = matches+1 if pixels == next_pixels else 0
            pixels = next_pixels
            if matches >= 2:
                break
        else:
            raise RuntimeError("Initial scene did not settle to a stable buffer")
    # Grayscale palette indices: a structural capture, not a colour screenshot.
    palette = b"".join(bytes([i, i, i, 0]) for i in range(256))
    rows = b"".join(pixels[y*width:(y+1)*width] + bytes((-width) % 4)
                    for y in reversed(range(height)))
    header = struct.pack("<2sIHHI", b"BM", 1078+len(rows), 0, 0, 1078)
    header += struct.pack("<IiiHHIIiiII", 40, width, height, 1, 8, 0, len(rows), 0, 0, 256, 0)
    destination.write_bytes(header + palette + rows)
    return hashlib.sha256(pixels).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--nasm", type=Path, default=ROOT / "build/tools/nasm-2.16.03/nasm.exe")
    parser.add_argument("--frame-only", action="store_true", help="Control copy without raster/copy timing observers")
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.destination, args.nasm, args.frame_only), indent=2))


if __name__ == "__main__":
    main()
