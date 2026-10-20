"""Validate weapon slots and preserve the timer cursor during weapon switches."""
import struct
from typing import Mapping

from ..models import Change, PatchAnalysis, WasdifyError
from ..signatures import find_matches
from .kremlin_wasd import segments, fail

# Shared RUS/ENG selector, through the availability and animation-state checks.
CONTEXT = bytes.fromhex(
    "8cd09045558bec1e8ed856578b560ac45e06268b87ce00"
    "c1e00203d826c49faa00268b878100c45e06"
    "268987d000263997d0000f84b5008bc2c1e002c45e0603d8"
    "268b87aa00260b87ac000f849d008b5e06268997d000268b87ce00"
    "c1e00203d8268b87aa00260b87ac00745a8b5e06268b87ce00"
    "c1e00203d826c49faa0026f6878300047e65"
)
BEGIN, END = 23, 41
ORIGINAL = CONTEXT[BEGIN:END]
REPLACEMENT = b"\x90" * (END - BEGIN)
EPILOGUE = bytes.fromhex("5f5e1f5d4dcb")

# Circular timer-list insertion. A callback may activate another object's first
# timer. Keep the dispatch cursor on the object whose array is being processed;
# the new node is already linked immediately after it. Empty-list initialization
# (the second store to the cursor) must remain intact.
TIMER_CONTEXT = bytes.fromhex(
    "26833f00755f833ee80300743fc41eec0366268b4708c45e066626894708"
    "66a1ec03662689470c26c45f088b46088b56062689470e2689570c"
    "c41eec03668b4606662689470866a3ec03eb15668b460666a3ec03"
    "c45e06662689470c6626894708ff06e803"
)
TIMER_BEGIN, TIMER_END = 0x46, 0x4a
TIMER_REPLACEMENT = b"\x90" * 4


def timer_contexts():
    return (TIMER_CONTEXT, TIMER_CONTEXT.replace(b"\xec\x03", b"\xf4\x03")
            .replace(b"\xe8\x03", b"\xf0\x03"))


class WeaponSelectionFix:
    def analyze(self, targets: Mapping[str, bytes]) -> PatchAnalysis:
        data = targets["main"]
        all_segments = segments(data)
        candidates = []
        pattern = " ".join("??" if BEGIN <= i < END else f"{v:02x}"
                           for i, v in enumerate(CONTEXT))
        for segment in all_segments:
            if segment.flags & (1 | 0x2000):
                continue
            for offset in find_matches(data, pattern, start=segment.start, end=segment.end):
                candidates.append((segment, offset))
        if len(candidates) != 1:
            raise WasdifyError("signature_count", expected=1, actual=len(candidates))
        owner, entry = candidates[0]
        if entry + 0xf2 > owner.end or data[entry+0xec:entry+0xf2] != EPILOGUE:
            fail()
        offset = entry + BEGIN
        actual = data[offset:entry+END]
        if actual not in (ORIGINAL, REPLACEMENT):
            raise WasdifyError("unexpected_bytes", offset=offset)
        timer_candidates = []
        for context in timer_contexts():
            pattern = " ".join("??" if TIMER_BEGIN <= i < TIMER_END else f"{v:02x}"
                               for i, v in enumerate(context))
            for segment in all_segments:
                if segment.flags & (1 | 0x2000):
                    continue
                for at in find_matches(data, pattern, start=segment.start, end=segment.end):
                    timer_candidates.append((segment, at, context))
        if len(timer_candidates) != 1:
            raise WasdifyError("signature_count", expected=1, actual=len(timer_candidates))
        timer_owner, timer_entry, timer_context = timer_candidates[0]
        timer_offset = timer_entry + TIMER_BEGIN
        timer_original = timer_context[TIMER_BEGIN:TIMER_END]
        timer_actual = data[timer_offset:timer_entry+TIMER_END]
        if timer_actual not in (timer_original, TIMER_REPLACEMENT):
            raise WasdifyError("unexpected_bytes", offset=timer_offset)
        # A loader fixup must never overwrite either replaced instruction range.
        for segment, begin, end in ((owner, offset, entry+END),
                                    (timer_owner, timer_offset, timer_entry+TIMER_END)):
            self._validate_fixups(data, segment, begin, end)
        changes = []
        if actual != REPLACEMENT:
            changes.append(Change("main", offset, ORIGINAL, REPLACEMENT))
        if timer_actual != TIMER_REPLACEMENT:
            changes.append(Change("main", timer_offset, timer_original, TIMER_REPLACEMENT))
        return PatchAnalysis(changes=tuple(changes), already_applied=not changes)

    @staticmethod
    def _validate_fixups(data, owner, begin, end):
        for kind, flags, source, _, _ in owner.records:
            width = {0: 1, 2: 2, 3: 4, 5: 2, 11: 6, 13: 4}.get(kind)
            if width is None:
                fail()
            seen = set()
            while source != 0xffff:
                if source in seen or source + max(width, 2) > owner.end-owner.start:
                    fail()
                seen.add(source)
                absolute = owner.start + source
                if absolute < end and absolute+width > begin:
                    fail()
                if flags & 4:
                    break
                source = struct.unpack_from("<H", data, absolute)[0]
