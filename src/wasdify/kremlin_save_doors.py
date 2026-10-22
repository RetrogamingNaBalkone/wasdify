"""Logical door fields and reconstruction of the derived image descriptor.

The native constructor copies an 18-byte resource image descriptor into +6Ch.
Its pixel pointer belongs to the fresh process and must remain a native binding.
Only the animation fraction is rebuilt, following the native image setter.
"""
from dataclasses import dataclass
import struct
from typing import Callable

from .kremlin_save import SaveFormatError
from .kremlin_save_events import NativeWrite


@dataclass(frozen=True)
class DoorState:
    flags: int
    marker: int
    position: int
    direction: int
    ready: int
    occupants: int
    steps: int


@dataclass(frozen=True)
class NativeDoor:
    address: int
    image_address: int
    image_pointer: bytes


def require(condition, code):
    if not condition:
        raise SaveFormatError(code)


def validate(state: DoorState):
    require(all(type(value) is int for value in vars(state).values()), "door_field")
    require(0 <= state.flags <= 0xffff and 0 <= state.marker <= 255, "door_field")
    require(state.direction in (1, 2) and state.ready in (0, 1), "door_state")
    require(0 <= state.steps <= 0x7fff and 0 <= state.position <= state.steps, "door_position")
    require(0 <= state.occupants <= 4096, "door_occupants")


def capture(read: Callable[[int, int], bytes], binding: NativeDoor) -> DoorState:
    address = binding.address
    require(binding.image_address == address + 0x6c and len(binding.image_pointer) == 4,
            "door_binding")
    require(read(address + 0x12, 2) == b"\x07\0"
            and read(address + 0x44, 4) == binding.image_pointer, "door_binding")
    flags, = struct.unpack("<H", read(address + 0x15, 2))
    marker, = struct.unpack("<B", read(address + 0x1b, 1))
    position, = struct.unpack("<h", read(address + 0x50, 2))
    direction, ready, occupants = struct.unpack("<BBh", read(address + 0x54, 4))
    steps, = struct.unpack("<h", read(address + 0x60, 2))
    state = DoorState(flags, marker, position, direction, ready, occupants, steps)
    validate(state)
    return state


def plan_restore(state: DoorState, read: Callable[[int, int], bytes], binding: NativeDoor):
    validate(state)
    # Verify the target's native type, self-image binding and existing logical
    # structure before proposing any modifications.
    capture(read, binding)
    values = ((0x15, struct.pack("<H", state.flags)), (0x1b, bytes((state.marker,))),
              (0x50, struct.pack("<h", state.position)),
              (0x54, struct.pack("<BBh", state.direction, state.ready, state.occupants)),
              (0x60, struct.pack("<h", state.steps)),
              # Native 10F8:0592: denominator zero becomes one; numerator is
              # clamped to the denominator. Existing resource flags/pixels stay.
              (0x72, struct.pack("<hh", state.steps or 1, min(state.position, state.steps or 1))))
    return tuple(NativeWrite(binding.address + offset, read(binding.address + offset, len(value)), value)
                 for offset, value in values)
