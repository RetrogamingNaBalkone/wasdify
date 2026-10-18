"""Masked byte signatures. Regions and results use file offsets, not addresses."""
from .models import WasdifyError


def compile_pattern(pattern: str) -> tuple[int | None, ...]:
    tokens = pattern.split()
    if not tokens or all(token == "??" for token in tokens):
        raise ValueError("Signature must contain at least one concrete byte")
    if any(token != "??" and (len(token) != 2 or any(
            c not in "0123456789abcdefABCDEF" for c in token)) for token in tokens):
        raise ValueError("Use two hex digits or ?? per byte")
    return tuple(None if token == "??" else int(token, 16) for token in tokens)


def find_matches(data: bytes, pattern: str, *, start: int = 0,
                 end: int | None = None) -> tuple[int, ...]:
    compiled = compile_pattern(pattern)
    end = len(data) if end is None else end
    if not 0 <= start <= end <= len(data):
        raise ValueError("Invalid signature region")
    anchor = next(i for i, value in enumerate(compiled) if value is not None)
    needle = bytes([compiled[anchor]])
    matches = []
    cursor = start + anchor
    while cursor < end:
        found = data.find(needle, cursor, end)
        if found < 0:
            break
        offset = found - anchor
        if offset + len(compiled) > end:
            break
        if all(value is None or data[offset + i] == value
               for i, value in enumerate(compiled)):
            matches.append(offset)
        cursor = found + 1
    return tuple(matches)


def require_matches(data: bytes, pattern: str, *, count: int = 1,
                    start: int = 0, end: int | None = None) -> tuple[int, ...]:
    if count < 1:
        raise ValueError("Expected signature count must be positive")
    matches = find_matches(data, pattern, start=start, end=end)
    if len(matches) != count:
        raise WasdifyError("signature_count", expected=count, actual=len(matches))
    return matches

