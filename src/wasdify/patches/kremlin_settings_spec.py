"""DOS configuration wire constants (no translated strings in the core)."""
from fractions import Fraction

# id, primary, secondary, edge-triggered, native command mask
ACTIONS = (
    ("forward", 0x11, 0x48, False, 0x10),
    ("backward", 0x1f, 0x50, False, 0x20),
    ("strafe_left", 0x1e, 0, False, 0x80000),
    ("strafe_right", 0x20, 0, False, 0x100000),
    ("turn_left", 0x4b, 0, False, 4),
    ("turn_right", 0x4d, 0, False, 8),
    ("strafe_modifier", 0x38, 0, False, 0),
    ("fire", 0x1d, 0, False, 0x200),
    ("use", 0x39, 0x1c, True, 0x800),
    ("run", 0x2a, 0x36, False, 0x2000),
    ("map", 0x0f, 0, True, 0),
    ("zoom_in", 0x4e, 0, True, 0x8000),
    ("zoom_out", 0x4a, 0, True, 0x10000),
    ("weapon_1", 2, 0, False, 0x400000),
    ("weapon_2", 3, 0, False, 0x800000),
    ("weapon_3", 4, 0, False, 0x1000000),
    ("pause", 0x19, 0, True, 0),
    ("menu", 1, 0, True, 0x4000),
    ("joystick", 0x24, 0, True, 0),
    ("save", 0x3f, 0, True, 0),
    ("load", 0x43, 0, True, 0),
)
# Native IRQ normalizes E0; the original reader sign-extends an 8-bit index.
# Unsupported holes, PrintScreen and Pause's multi-byte sequences are excluded.
SCANCODES = tuple(range(1, 0x54)) + (0x57, 0x58)
VIDEO, SENSITIVITY, VERSION, VALID, SECTION = 16, 18, 20, 22, 23
BINDINGS, STATES, PATH, TEMP_PATH, BACKUP_PATH = 32, 80, 128, 400, 672
BUFFER, LINE, OUTPUT, WORK = 1024, 5184, 5376, 8192
DATA_SIZE = 12288
FILE_LIMIT, LINE_LIMIT = 4096, 127


def sensitivity(step: int) -> Fraction:
    if not 0 <= step <= 9:
        raise ValueError("sensitivity")
    return Fraction(6, 5 * (13 - step))


def initial_data() -> bytes:
    import struct
    data = bytearray(DATA_SIZE)
    data[:16] = b"WASDify-CFG-v1\0\0"
    struct.pack_into("<hhH", data, VIDEO, 0, 7, 0)
    for i, (_, first, second, _, _) in enumerate(ACTIONS):
        data[BINDINGS + i * 2:BINDINGS + i * 2 + 2] = bytes((first, second))
    return bytes(data)
