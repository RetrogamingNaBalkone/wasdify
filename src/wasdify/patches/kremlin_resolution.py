"""Restore -r1's VBE 100h mode in the 16-bit NE editions of Kremlin.

Only the immediate's low byte changes. The complete four-branch selector is
validated inside NE code segments; offsets and whole-file hashes are not gates.
"""
import struct
from typing import Mapping

from ..models import Change, PatchAnalysis, WasdifyError
from ..signatures import find_matches

# cmp argument, 1..4; initialize VESA; push mode; call common mode setter.
# Only -r1's low immediate byte is variable (00 patched / 01 original).
SELECTOR = (
    "83 7E 06 01 75 13 0E E8 F1 FD 0B C0 74 0B 68 ?? 01 "
    "0E E8 DA 05 90 83 C4 02 "
    "83 7E 06 02 75 13 0E E8 D8 FD 0B C0 74 0B 68 01 01 "
    "0E E8 C1 05 90 83 C4 02 "
    "83 7E 06 03 75 13 0E E8 BF FD 0B C0 74 0B 68 03 01 "
    "0E E8 A8 05 90 83 C4 02 "
    "83 7E 06 04 75 13 0E E8 A6 FD 0B C0 74 0B 68 05 01 "
    "0E E8 8F 05 90 83 C4 02"
)
IMMEDIATE = 15


def code_regions(data: bytes) -> tuple[tuple[int, int], ...]:
    """Bound the search to intact, nonoverlapping, file-backed NE segments."""
    def invalid():
        raise WasdifyError("unsupported_executable")

    if len(data) < 64 or data[:2] != b"MZ":
        invalid()
    ne = struct.unpack_from("<I", data, 0x3C)[0]
    if ne < 64 or ne + 64 > len(data) or data[ne:ne + 2] != b"NE":
        invalid()
    count = struct.unpack_from("<H", data, ne + 0x1C)[0]
    relative_table = struct.unpack_from("<H", data, ne + 0x22)[0]
    shift = struct.unpack_from("<H", data, ne + 0x32)[0]
    table = ne + relative_table
    if not count or relative_table < 64 or shift > 16 or table + count * 8 > len(data):
        invalid()
    regions = []
    occupied = []
    for index in range(count):
        sector, length, flags, _ = struct.unpack_from("<4H", data, table + index * 8)
        if not sector:  # Uninitialized segment: no searchable bytes in the file.
            continue
        start = sector << shift
        end = start + (length or 65536)
        if start < table + count * 8 or end > len(data):
            invalid()
        occupied.append((start, end))
        if not flags & (0x0001 | 0x2000):  # Data / 32-bit segment.
            regions.append((start, end))
    occupied.sort()
    if any(left[1] > right[0] for left, right in zip(occupied, occupied[1:])):
        invalid()
    return tuple(regions)


def locate(data: bytes) -> int:
    matches = tuple(offset for start, end in code_regions(data)
                    for offset in find_matches(data, SELECTOR, start=start, end=end))
    if len(matches) != 1:
        raise WasdifyError("signature_count", expected=1, actual=len(matches))
    offset = matches[0] + IMMEDIATE
    if data[offset] not in (0, 1):
        raise WasdifyError("unexpected_bytes", offset=offset)
    return offset


def probe(targets: Mapping[str, bytes]) -> bool:
    try:
        locate(targets["main"])
    except WasdifyError:
        return False
    return True


class Resolution640x400:
    def analyze(self, targets: Mapping[str, bytes]) -> PatchAnalysis:
        data = targets["main"]
        offset = locate(data)
        if data[offset] == 0:
            return PatchAnalysis(already_applied=True)
        return PatchAnalysis(changes=(Change("main", offset, b"\x01", b"\x00"),))
