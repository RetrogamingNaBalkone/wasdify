"""Read-only NE disassembly for input research (development-only Capstone)."""
import argparse
from pathlib import Path
import struct


def segments(data):
    ne = struct.unpack_from("<I", data, 60)[0]
    table = ne + struct.unpack_from("<H", data, ne + 34)[0]
    shift = struct.unpack_from("<H", data, ne + 50)[0]
    for index in range(struct.unpack_from("<H", data, ne + 28)[0]):
        sector, size, flags, minimum = struct.unpack_from("<4H", data, table + index * 8)
        yield index + 1, sector << shift, size or 65536, flags, minimum


def main():
    from capstone import Cs, CS_ARCH_X86, CS_MODE_16
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument("segment", type=int, nargs="?")
    parser.add_argument("start", type=lambda v: int(v, 0), nargs="?", default=0)
    parser.add_argument("end", type=lambda v: int(v, 0), nargs="?", default=65536)
    args = parser.parse_args()
    data = args.exe.read_bytes()
    for index, base, size, flags, minimum in segments(data):
        if args.segment is None:
            print(index, hex(base), hex(size), hex(flags), hex(minimum))
        elif index == args.segment:
            md = Cs(CS_ARCH_X86, CS_MODE_16)
            md.skipdata = True
            for ins in md.disasm(data[base + args.start:base + min(size, args.end)], args.start):
                print(f"{ins.address:04x} {base + ins.address:06x} {ins.bytes.hex():24} {ins.mnemonic:8} {ins.op_str}")
            if flags & 0x100:
                count = struct.unpack_from("<H", data, base + size)[0]
                print("Relocations:", count)
                for n in range(count):
                    record = struct.unpack_from("<BBHHH", data, base + size + 2 + n * 8)
                    print(record)


if __name__ == "__main__":
    main()
