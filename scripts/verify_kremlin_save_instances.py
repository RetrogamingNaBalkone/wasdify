"""Create and free a native RUS projectile while the world remains stopped.

Uses the researched constructors/destructors in a disposable executable. It
does not save a game or activate the created projectile. Native pointers never
come from a file. Full reports stay in ignored mqa/.
"""
import argparse
from pathlib import Path
import struct

from inspect_kremlin_save_world import Memory, observe
from profile_kremlin import ROOT
from verify_kremlin_save_io import trial
from verify_kremlin_settings_dosbox import wait_for
from wasdify.kremlin_save_events import NativeWrite, capture


def native_call(api, transport, operation, template, target):
    state = api.get(f"memory/{transport + 44}/96")
    if state[:2] not in (b"\x01\0", b"\x02\0", b"\x03\0"):
        raise ValueError("Instance operation requires the main-loop safe gate")
    expected = state[72:88]  # +116…131: command, completion, status and arguments
    if expected[:2] != bytes(2):
        raise ValueError("Native command already pending")
    command = bytearray(expected)
    struct.pack_into("<H", command, 0, operation)
    struct.pack_into("<HH", command, 12, template, target)
    # Publish arguments and command together; retain the completion counter.
    api.put(str(transport + 116), bytes(command), expected=expected)

    def completed():
        result = api.get(f"memory/{transport + 118}/22")
        return result if result[:2] != expected[2:4] else None

    result = wait_for(completed, poll_interval=.005)
    return struct.unpack_from("<H", result, 2)[0], result[18:22]


def run(nasm):
    def observer(api, transport, language, level):
        def exercise(memory, objects, queue, callbacks, now, doors):
            ds = queue.object_count_address - 0x3e8
            world = memory.pointer(ds + 0x266a, 0x74a)
            template = next(key - 1 for key, obj in objects.items() if obj.native_type == 8)
            target = next(i for i in range(256) if i + 1 not in objects)
            player = next(key - 1 for key, obj in objects.items() if obj.native_type == 1)
            table_entry = world + 0x30e + target * 4
            queue_before = api.get(f"memory/{queue.object_count_address}/13")
            count_before = api.get(f"memory/{world + 0x73c}/2")
            events = capture(memory.read, objects, queue, callbacks, now)
            assert native_call(api, transport, 1, 256, target)[0] == 1
            assert native_call(api, transport, 1, player, target)[0] == 1
            assert native_call(api, transport, 1, template, 256)[0] == 1
            assert native_call(api, transport, 99, template, target)[0] == 1
            error, pointer = native_call(api, transport, 1, template, target)
            assert error == 0 and pointer != bytes(4)
            assert pointer == api.get(f"memory/{table_entry}/4")
            current = Memory(api.get("memory/0/0x2000000"), memory.ldt)
            address = current.pointer(table_entry, 0x83)
            source = objects[template + 1]
            assert pointer != source.pointer
            assert current.u16(address) == 0 and current.u16(address + 2) == 4
            assert current.u16(address + 0x12) == 8
            assert current.read(address + 0x14, 1) == bytes((target,))
            assert current.read(address + 0x48, 4) == memory.read(source.address + 0x48, 4)
            assert current.read(address + 0x4c, 4) != memory.read(source.address + 0x4c, 4)
            assert api.get(f"memory/{queue.object_count_address}/13") == queue_before
            assert native_call(api, transport, 1, template, target)[0] == 2
            assert native_call(api, transport, 2, 0, player)[0] == 1
            assert native_call(api, transport, 2, 0, target)[0] == 0
            assert api.get(f"memory/{table_entry}/4") == bytes(4)
            assert native_call(api, transport, 2, 0, target)[0] == 1
            assert api.get(f"memory/{queue.object_count_address}/13") == queue_before
            count_after = api.get(f"memory/{world + 0x73c}/2")
            assert struct.unpack("<H", count_after)[0] == struct.unpack("<H", count_before)[0] + 1
            # Native reservation and release are separate from the snapshot's
            # logical active-projectile counter. Restore that counter explicitly.
            plan = (NativeWrite(world + 0x73c, count_after, count_before),)
            return plan, events, doors, {"native_instance_created_and_freed": True,
                "template_index": template, "target_index": target,
                "constructor_scheduled_events": False, "invalid_requests_rejected": 7}
        return observe(api, transport, language, level, components=exercise)
    return trial("ru", nasm, observer=observer, world_research=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nasm", type=Path, default=ROOT / "build/tools/nasm-2.16.03/nasm.exe")
    run(parser.parse_args().nasm)
