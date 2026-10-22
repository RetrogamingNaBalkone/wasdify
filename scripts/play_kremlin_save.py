"""Interactive RUS level-1 research prototype; not the released save-load patch.

The Python bridge performs logical serialization through the local DOSBox API.
Native code supplies the safe frame gate, object construction and game messages.
Preparation assembles once; the generated launcher needs no runtime assembler.
Originals and installed games are never modified or included in a deliverable.
"""
import argparse
from collections import Counter
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import traceback

from inspect_kremlin_save_world import Memory, observe, apply_component_plan, pause_at_boundary, locate_ldt
from profile_kremlin import ROOT, capture
from verify_kremlin_save_instances import native_call
from verify_kremlin_save_io import trial
from verify_kremlin_settings_dosbox import wait_for
from verify_kremlin_save_restart import inventory, resource_hashes, door_ids
from verify_kremlin_save_state import world_address, frame_objects, resources_at_start
from wasdify import kremlin_save_state as state_adapter
from wasdify import kremlin_save_events as event_adapter
from wasdify import kremlin_save_doors as door_adapter
from wasdify.i18n import Translator
from wasdify.kremlin_save import EventRecord


REVISION = "WASDify-rus1-prototype-1"
MAX_SNAPSHOT = 1024 * 1024


class DeathChoicePending(Exception):
    """A death check won the race with a keyboard save/load request."""


@contextmanager
def session_log(path):
    """Keep console output and tracebacks even when the launcher window closes."""
    class Tee:
        def __init__(self, console, log):
            self.console, self.log = console, log

        def write(self, value):
            self.log.write(value)
            self.log.flush()
            return self.console.write(value)

        def flush(self):
            self.log.flush()
            self.console.flush()

    with path.open("a", encoding="utf-8") as log:
        log.write(f"\n--- {datetime.now().isoformat()} {REVISION} ---\n")
        log.flush()
        with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
            try:
                yield
            except Exception:
                traceback.print_exc(file=log)
                raise


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate_json_key")
    return value


def _decode(raw):
    if not 0 < len(raw) <= MAX_SNAPSHOT:
        raise ValueError("snapshot_size")
    envelope = json.loads(raw, object_pairs_hook=unique_object)
    if set(envelope) != {"sha256", "payload"}:
        raise ValueError("snapshot_envelope")
    payload = envelope["payload"]
    if hashlib.sha256(canonical(payload)).hexdigest() != envelope["sha256"]:
        raise ValueError("snapshot_checksum")
    sections = {"revision", "resources", "baseline_inventory", "state", "events", "doors"}
    if set(payload) not in (sections, sections | {"next_attempt"}):
        raise ValueError("snapshot_sections")
    if "next_attempt" in payload and (type(payload["next_attempt"]) is not int or
                                     not 1 <= payload["next_attempt"] <= 32767):
        raise ValueError("snapshot_attempt")
    if payload["revision"] != REVISION:
        raise ValueError("snapshot_revision")
    payload["state"]["maps"] = [bytes.fromhex(layer) for layer in payload["state"]["maps"]]
    payload["state"] = state_adapter.with_floor_defaults(payload["state"])
    return payload


def decode(raw):
    try:
        return _decode(raw)
    except (KeyError, TypeError, AttributeError, UnicodeError) as exc:
        raise ValueError("snapshot_structure") from exc


def encode(payload):
    wire = {**payload, "state": {**payload["state"], "maps": [layer.hex() for layer in payload["state"]["maps"]]}}
    raw = canonical({"payload": wire, "sha256": hashlib.sha256(canonical(wire)).hexdigest()})
    decode(raw)
    return raw


def store_snapshot(path, payload):
    """Preserve the last verified file on every failure before replacement."""
    raw = encode(payload)
    temporary = path.with_suffix(".TMP")
    previous = path.with_suffix(".PRV")
    backup_temp = path.with_suffix(".BKT")
    old = path.read_bytes() if path.exists() else None
    if old is not None:
        decode(old)
    created = []
    try:
        with temporary.open("xb") as stream:
            created.append(temporary)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        decode(temporary.read_bytes())
        if old is not None:
            with backup_temp.open("xb") as stream:
                created.append(backup_temp)
                stream.write(old)
                stream.flush()
                os.fsync(stream.fileno())
            if backup_temp.read_bytes() != old:
                raise ValueError("snapshot_backup_verification")
            os.replace(backup_temp, previous)
        if (path.read_bytes() if path.exists() else None) != old:
            raise ValueError("snapshot_changed_before_replace")
        os.replace(temporary, path)
        if path.read_bytes() != raw:
            raise ValueError("snapshot_final_verification")
        decode(path.read_bytes())
    finally:
        for owned in created:
            owned.unlink(missing_ok=True)


class Session:
    def __init__(self, api, transport, translator, snapshot=None):
        self.api, self.transport, self.tr = api, transport, translator
        self.resources = None
        self.manifest = resource_hashes()
        self.allow_load = True
        self.snapshot_path = snapshot
        self.reports = []
        self.game_messages = json.loads(files("wasdify").joinpath(
            "data/kremlin_save_prototype_messages.json").read_text("utf-8"))["ru"]

    def message(self, key):
        raw = self.game_messages[key].encode("cp866")
        if len(raw) > 38 or b"\0" in raw:
            raise ValueError("native_message_size")
        self.api.put(str(self.transport + 256), raw + bytes(39 - len(raw)))
        if native_call(self.api, self.transport, 3, 0, 0)[0]:
            raise ValueError("native_message_failed")

    def bind(self, *, startup=False, manual_start=False):
        def initial(memory, objects, queue, callbacks, now, doors):
            self.resources = resources_at_start(memory, objects, queue)
            self.ldt = memory.ldt
            self.baseline_inventory = inventory(objects)
            self.ds, self.world = world_address(memory, queue)
            self.queue, self.callbacks = queue, callbacks
            self.live = memory.u16(self.transport + 152) == 1
            return (), event_adapter.capture(memory.read, objects, queue, callbacks, now), doors, {}
        observe(self.api, self.transport, "ru", 1, components=initial,
                keep_gate=startup, manual_start=manual_start)
        self.startup_gate = startup

    def wait_for_level_selection(self):
        initial = self.api.get("memory/0/0x2000000")
        anchor = b"OVERFLOW\0UNDERFLOW\0TLOSS\0PLOSS"
        if initial.count(anchor) != 1:
            raise ValueError("Native data anchor is not unique")
        self.ds = initial.find(anchor) - 0x1082
        self.ldt = locate_ldt(initial, self.ds, self.transport)
        self.live = True
        self.world = None
        while self.api.get(f"memory/{self.transport + 44}/2") != b"\x03\0":
            time.sleep(.02)
        view = Memory(self.api.get("memory/0/0x2000000"), self.ldt)
        resource = view.pointer(self.ds + 0x1f82, 7)
        self.selected_resource = view.read(resource, 7).split(b"\0")[0]
        if self.selected_resource != b"LEVEL1":
            raise ValueError("prototype_level_unsupported")

    def finish_early_choice(self, action):
        self.clear_keyboard()
        loading = action == "load"
        self.api.put(str(self.transport + 158), struct.pack("<H", int(loading)))
        self.api.put(str(self.transport + 16), struct.pack("<H", 13 if loading else 17))
        self.release_gate()

    def start_from_selection(self):
        self.wait_for_level_selection()
        action = self.choose_level_action(early=True)
        self.bind(startup=True, manual_start=True)
        if action == "load":
            if native_call(self.api, self.transport, 6, 0, 0)[0]:
                raise RuntimeError("native_palette_failed")
            self.load(self.snapshot_path)
            self.refresh_available()
        else:
            self.refresh_available()
            self.release_gate()
        self.startup_gate = False
        return action

    def progress(self, key):
        self.pause_for_action()
        try:
            self.message(key)
        finally:
            self.api.put(str(self.transport + 46), b"\x01\0")
            wait_for(lambda: self.api.get(f"memory/{self.transport + 44}/2") == bytes(2),
                     poll_interval=.005)
        # The following operation stops at the next main-loop boundary: one
        # normal frame paints the message before capture/installation starts.
        # Setting the dirty HUD flag inside the operation's held frame would
        # leave the old screen visible throughout the whole I/O pause.

    def current_world(self, memory, objects, queue):
        if world_address(memory, queue) != (self.ds, self.world):
            raise ValueError("prototype_world_changed")
        for key, baseline in self.resources.baseline.items():
            if baseline.native_type == 8:
                if key in objects and objects[key].native_type != 8:
                    raise ValueError("prototype_baseline_object_changed")
                continue
            if key not in objects or objects[key].pointer != baseline.pointer or objects[key].native_type != baseline.native_type:
                raise ValueError("prototype_baseline_object_changed")

    def input_plan(self, memory):
        interface = memory.pointer(self.ds + 0xa5d6, 0x2c)
        return (event_adapter.NativeWrite(interface + 0x28, memory.read(interface + 0x28, 4), bytes(4)),)

    def clear_keyboard(self):
        # IRQ input remains live while the main loop is held. It is deliberately
        # reset, not restored from the earlier memory image or compared with it.
        self.api.put(str(self.ds + 0x2152), bytes(131))

    def pause_for_action(self):
        if pause_at_boundary(self.api, self.transport, allow_death=self.live) == b"\x02\0":
            raise DeathChoicePending()

    def release_gate(self):
        self.api.put(str(self.transport + 46), b"\x01\0")
        wait_for(lambda: self.api.get(f"memory/{self.transport + 44}/2") == bytes(2),
                 poll_interval=.005)

    def validate_packet(self, packet, memory):
        records = packet["state"]["objects"]
        state_adapter.construction_plan(packet["state"], self.resources)
        proposed = {}
        for record in records:
            key, template = record["id"], record["template"]
            if record["type"] == 8 and template in self.resources.baseline:
                proposed[key] = replace(self.resources.baseline[template], object_id=key)
            elif key in self.resources.baseline:
                proposed[key] = self.resources.baseline[key]
            else:
                raise ValueError("snapshot_native_construction_unsupported")
        state_adapter.plan_restore(packet["state"], memory.read, proposed, self.resources,
                                   self.ds, self.world, memory.u32(self.transport + 52))
        events = tuple(EventRecord(item["event_id"], item["owner_id"], item["event_type"],
                       item["event_version"], {int(key): value for key, value in item["fields"].items()})
                       for item in packet["events"])
        event_adapter.validate(events, proposed)
        dormant = {r["id"] for r in records if r["type"] == 9
                   and self.resources.placed_actors is not None
                   and r["id"] not in self.resources.placed_actors and r["fields"]["ai_state"] == 0}
        if any(event.owner_id in dormant for event in events):
            raise ValueError("inactive_unplaced_actor_events")
        by_id = {}
        for key, obj in self.resources.baseline.items():
            if obj.native_type == 7:
                by_id[key] = door_adapter.NativeDoor(obj.address, memory.pointer(obj.address + 0x44, 18),
                                                     memory.read(obj.address + 0x44, 4))
        if len(packet["doors"]) != len(by_id) or {item["id"] for item in packet["doors"]} != set(by_id):
            raise ValueError("snapshot_door_inventory")
        restored = [(by_id[item["id"]], door_adapter.DoorState(**item["state"])) for item in packet["doors"]]
        for binding, saved in restored:
            door_adapter.plan_restore(saved, memory.read, binding)
        return events, restored

    def reset_world(self):
        error, _ = native_call(self.api, self.transport, 4, 0, 0)
        if error:
            raise RuntimeError(f"native_world_reset_failed_{error}")
        memory = Memory(self.api.get("memory/0/0x2000000"), self.ldt)
        self.world = memory.pointer(self.ds + 0x266a, 0x74a)
        objects = frame_objects(memory, self.world)
        self.resources = resources_at_start(memory, objects, self.queue)
        self.baseline_inventory = inventory(objects)
        self.clear_keyboard()
        return memory, objects

    def write_dialog(self, text):
        raw = text.encode("cp866")
        if len(raw) > 255 or b"\0" in raw:
            raise ValueError("native_dialog_size")
        self.api.put(str(self.transport + 1536), raw + bytes(256 - len(raw)))

    def draw_dialog(self, text):
        if self.world is None:
            self.write_dialog(text)
            if native_call(self.api, self.transport, 5, 0, 0)[0]:
                raise RuntimeError("native_dialog_failed")
            return
        view = Memory(self.api.get("memory/0/0x2000000"), self.ldt)
        width, height = struct.unpack("<HH", view.read(self.ds + 0xc1fc, 4))
        if not (160 <= width <= 1600 and 100 <= height <= 1200):
            raise ValueError("native_dialog_dimensions")
        # Native drawing uses 64 KiB aliases over the contiguous frame buffer;
        # resolve the first byte as in profile_kremlin.capture, then bound the
        # complete linear image against observed guest RAM, not one alias limit.
        buffer = view.pointer(self.ds + 0xb53a, 1)
        if buffer is None or buffer + width * height > len(view.data):
            raise ValueError("native_dialog_buffer")
        # Use the game's own HUD background colour. Clear the whole panel on
        # each selection change so transparent space glyphs erase the old cursor.
        shade = Counter(view.read(buffer + width * (height * 5 // 6), width)).most_common(1)[0][0]
        top, bottom, left = height * 3 // 10, height * 7 // 10, max(4, width // 64)
        panel = bytearray(view.read(buffer + top * width, (bottom - top) * width))
        row = b"\0" + bytes((shade,)) * (width - 2 * left - 2) + b"\0"
        for index in range(bottom - top):
            pixels = bytes(len(row)) if index in (0, bottom - top - 1) else row
            panel[index * width + left:(index + 1) * width - left] = pixels
        self.api.put(str(buffer + top * width), bytes(panel))
        self.write_dialog(text)
        if native_call(self.api, self.transport, 5, 0, 0)[0]:
            raise RuntimeError("native_dialog_failed")

    def choice_text(self, selected=1):
        labels = (self.game_messages["choice_start"], self.game_messages["choice_load"])
        return (self.game_messages["choice_title"] + "\n\n" +
                "\n".join(("> " if index == selected else "  ") + label for index, label in enumerate(labels)) +
                "\n\n" + self.game_messages["choice_hint"])

    def refresh_available(self):
        available = False
        if self.snapshot_path is not None:
            try:
                packet = decode(self.snapshot_path.read_bytes())
                available = packet["resources"] == self.manifest and (
                    self.resources is None or packet["baseline_inventory"] == self.baseline_inventory)
            except (OSError, ValueError):
                pass
        self.has_save = available
        if self.live:
            self.write_dialog(self.choice_text())
            self.api.put(str(self.transport + 144), struct.pack("<H", int(available)))
        return available

    def restart_level(self):
        try:
            memory, objects = self.reset_world()
            state = state_adapter.capture(memory.read, objects, self.resources, self.ds, self.world,
                                          memory.u32(self.transport + 52))
            interface = memory.pointer(self.ds + 0xa5d6, 0x2c)
            plan = state_adapter.plan_hud(state, memory.read, interface) + self.input_plan(memory)
            apply_component_plan(self.api, plan, workers=16)
            self.message("cleared")
            if native_call(self.api, self.transport, 7, 0, 0)[0]:
                raise RuntimeError("native_restart_intro_failed")
            self.release_gate()
        except Exception as exc:
            raise RuntimeError("prototype_level_restart_interrupted") from exc
        self.allow_load = True
        self.reports.append({"restart": {"native_world_recreated": True}})

    def choose_level_action(self, *, death=False, early=False):
        if not self.live:
            return None
        if not self.refresh_available():
            if early:
                self.finish_early_choice(None)
                return None
            if death or getattr(self, "startup_gate", False):
                self.release_gate()   # death hook replays native health/life handling
            self.startup_gate = False
            return None
        if not death and not early and not getattr(self, "startup_gate", False):
            pause_at_boundary(self.api, self.transport, allow_death=True)
        self.startup_gate = False
        view = Memory(self.api.get("memory/0/0x2000000"), self.ldt)
        hero = view.pointer(self.world + 0x442, 0xde) if self.world is not None else None
        reason = "death" if death else "startup"
        health, lives = (view.u16(hero + 0x5e), view.u16(hero + 0xd6)) if hero is not None else (None, None)
        self.reports.append({"choice": {"reason": reason, "health": health, "lives": lives}})
        print(f"level_choice reason={reason} health={health} lives={lives}", flush=True)
        self.clear_keyboard()
        if early and native_call(self.api, self.transport, 6, 0, 0)[0]:
            raise RuntimeError("native_menu_palette_failed")
        selected = 1
        self.draw_dialog(self.choice_text(selected))
        pressed = set()
        while True:
            for scan in (0x48, 0x50, 0x1c, 0x43):
                at = self.ds + 0x2155 + scan
                value = self.api.get(f"memory/{at}/1")
                if value == b"\x03":
                    pressed.add(scan)
                    continue
                if value != b"\x02":
                    continue
                self.api.put(str(at), bytes(1), expected=b"\x02")
                # Releasing the Enter that dismissed the native intro must
                # not confirm this newly opened menu. Require a new press.
                if scan not in pressed:
                    continue
                pressed.remove(scan)
                if scan in (0x48, 0x50):
                    selected = 1 - selected
                    self.draw_dialog(self.choice_text(selected))
                elif selected == 1 or scan == 0x43:
                    if early:
                        self.finish_early_choice("load")
                        return "load"
                    self.load(self.snapshot_path)
                    self.refresh_available()
                    return "load"
                else:
                    if early:
                        self.finish_early_choice("restart")
                        return "restart"
                    self.restart_level()
                    self.refresh_available()
                    return "restart"
            time.sleep(.02)

    def save(self, path):
        started = time.perf_counter()
        self.progress("saving")
        def save_at_gate(memory, objects, queue, callbacks, now, doors):
            self.current_world(memory, objects, queue)
            state = state_adapter.capture(memory.read, objects, self.resources, self.ds, self.world,
                                          memory.u32(self.transport + 52))
            state_adapter.plan_restore(state, memory.read, objects, self.resources, self.ds, self.world,
                                       memory.u32(self.transport + 52))
            state_adapter.construction_plan(state, self.resources)
            events = event_adapter.capture(memory.read, objects, queue, callbacks, now)
            packet = {"revision": REVISION, "resources": self.manifest,
                      "next_attempt": memory.u16(self.ds + 0x1fae),
                      "baseline_inventory": self.baseline_inventory, "state": state,
                      "events": [asdict(event) for event in events],
                      "doors": [{"id": key, "state": asdict(saved)}
                                for key, (_, saved) in sorted(door_ids(objects, doors).items())]}
            store_snapshot(path, packet)
            self.allow_load = False
            if self.snapshot_path is not None:
                self.refresh_available()
            self.clear_keyboard()
            self.message("saved")
            return self.input_plan(memory), events, doors, {"snapshot_written": True}
        result = observe(self.api, self.transport, "ru", 1, components=save_at_gate, dismiss_intro=False,
                         runtime_layout=(self.ds, self.ldt))
        result["elapsed_seconds"] = time.perf_counter() - started
        self.reports.append({"save": result})
        return result

    def load(self, path):
        started = time.perf_counter()
        if not self.live and not self.allow_load:
            raise ValueError(self.tr("prototype_load_fresh_only"))
        packet = decode(path.read_bytes())
        if packet["resources"] != self.manifest or packet["baseline_inventory"] != self.baseline_inventory:
            raise ValueError("prototype_incompatible_resources")
        context = {"mutating": False}
        entered_held = self.api.get(f"memory/{self.transport + 44}/2") in (b"\x01\0", b"\x02\0")
        if not entered_held:
            self.progress("loading")
        elif self.live:
            self.draw_dialog(self.game_messages["loading"])
        if self.live:
            if not entered_held:
                self.pause_for_action()
            try:
                before = Memory(self.api.get("memory/0/0x2000000"), self.ldt)
                self.current_world(before, frame_objects(before, self.world), self.queue)
                self.validate_packet(packet, before)
                context["mutating"] = True
                self.reset_world()
            except Exception as exc:
                if not entered_held and not context["mutating"]:
                    self.release_gate()
                if context["mutating"]:
                    raise RuntimeError("prototype_world_reset_interrupted") from exc
                raise

        def load_at_gate(memory, objects, queue, callbacks, now, doors):
            self.current_world(memory, objects, queue)
            if inventory(objects) != self.baseline_inventory:
                raise ValueError(self.tr("prototype_load_fresh_only"))
            records = packet["state"]["objects"]
            wall = memory.u32(self.transport + 52)
            events, restored_doors = self.validate_packet(packet, memory)
            remove, clones = state_adapter.construction_plan(packet["state"], self.resources)
            for key in remove:
                context["mutating"] = True
                if native_call(self.api, self.transport, 2, 0, key - 1)[0]:
                    raise RuntimeError("native_removal_failed")
            for key, template in clones:
                context["mutating"] = True
                error, _ = native_call(self.api, self.transport, 1, template - 1, key - 1)
                if error:
                    raise RuntimeError(f"native_construction_failed_{error}")
            current = Memory(self.api.get("memory/0/0x2000000"), memory.ldt)
            objects.clear()
            objects.update(frame_objects(current, self.world))
            plan = state_adapter.plan_restore(packet["state"], current.read, objects, self.resources,
                                             self.ds, self.world, wall)
            plan += event_adapter.plan_restore(events, current.read, objects, queue, callbacks, now)
            for binding, saved in restored_doors:
                plan += door_adapter.plan_restore(saved, current.read, binding)
            interface = current.pointer(self.ds + 0xa5d6, 0x2c)
            plan += state_adapter.plan_hud(packet["state"], current.read, interface)
            plan += self.input_plan(current)
            attempt = packet.get("next_attempt", max(2, current.u16(self.ds + 0x1fae)))
            plan += (event_adapter.NativeWrite(self.ds + 0x1fae,
                current.read(self.ds + 0x1fae, 2), struct.pack("<H", attempt)),)
            context["mutating"] = True
            apply_component_plan(self.api, plan, workers=16)
            check = Memory(self.api.get("memory/0/0x2000000"), memory.ldt)
            if state_adapter.capture(check.read, objects, self.resources, self.ds, self.world, wall) != packet["state"]:
                raise RuntimeError("prototype_state_verification_failed")
            for change in state_adapter.plan_hud(packet["state"], check.read, interface):
                if change.expected != change.replacement:
                    raise RuntimeError("prototype_hud_verification_failed")
            if event_adapter.capture(check.read, objects, queue, callbacks, now) != events:
                raise RuntimeError("prototype_event_verification_failed")
            if any(door_adapter.capture(check.read, binding) != saved for binding, saved in restored_doors):
                raise RuntimeError("prototype_door_verification_failed")
            self.allow_load = False
            self.clear_keyboard()
            self.message("loaded")
            return (), events, restored_doors, {"restored": True, "hud_verified": True,
                "snapshot_objects": len(records), "native_clones": sum(bool(record["template"]) for record in records)}
        try:
            result = observe(self.api, self.transport, "ru", 1, components=load_at_gate, dismiss_intro=False,
                             runtime_layout=(self.ds, self.ldt), gate_held=self.live or entered_held)
        except Exception as exc:
            if context["mutating"]:
                # A partial installation must not return to the interactive loop
                # as a recoverable file error and continue an inconsistent world.
                raise RuntimeError("prototype_load_interrupted") from exc
            raise
        result["elapsed_seconds"] = time.perf_counter() - started
        self.reports.append({"load": result})
        return result

    def request(self):
        if self.live and self.api.get(f"memory/{self.transport + 146}/2") == b"\x01\0":
            return "death"
        pending = []
        for scan, operation in ((0x3f, "save"), (0x43, "load")):
            at = self.ds + 0x2155 + scan
            # Native single-press actions use release state 2. It survives short
            # taps and cannot repeat while the key is held (1070:02AB).
            if self.api.get(f"memory/{at}/1") == b"\x02":
                self.api.put(str(at), bytes(1), expected=b"\x02")
                pending.append(operation)
        if len(pending) > 1:
            raise ValueError("simultaneous_save_load")
        return pending[0] if pending else None


def prepare(directory, nasm, language):
    directory.mkdir(parents=True, exist_ok=True)
    payload = directory / "world-ru.bin"
    subprocess.run([str(nasm), "-f", "bin", "-I", str(ROOT / "src/wasdify/patches") + "/",
                    str(ROOT / "src/wasdify/patches/kremlin_save_world.asm"), "-o", str(payload)], check=True)
    (directory / "launcher.json").write_text(json.dumps({"payload_sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
        "experimental": True, "native_serializer": False, "language": "ru", "level": 1}, indent=2), encoding="ascii")
    command = directory / "start.cmd"
    command.write_text(f'@echo off\nsetlocal\ncd /d "{ROOT}"\n'
        f'set "PYTHONPATH={ROOT / "src"};{ROOT / "scripts"}"\n'
        f'"{sys.executable}" "{ROOT / "scripts/play_kremlin_save.py"}" --directory "{directory}" --lang {language}\npause\n', encoding="ascii")
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "mqa/save-play-ru")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--lang", choices=("ru", "en"), default="ru")
    parser.add_argument("--nasm", type=Path, default=ROOT / "build/tools/nasm-2.16.03/nasm.exe")
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.relative_to((ROOT / "mqa").resolve())
    tr = Translator(args.lang)
    if args.prepare:
        print(tr("prototype_prepared", path=prepare(directory, args.nasm, args.lang)))
        return
    payload = directory / "world-ru.bin"
    metadata = json.loads((directory / "launcher.json").read_text("ascii"))
    if hashlib.sha256(payload.read_bytes()).hexdigest() != metadata["payload_sha256"]:
        raise ValueError("prototype_payload_changed")
    snapshot = directory / "KRS1.PROTO"
    print(tr("prototype_intro"), flush=True)

    def observer(api, transport, language, level):
        session = Session(api, transport, tr, snapshot if not args.smoke else None)
        try:
            if args.smoke:
                session.bind()
            else:
                session.start_from_selection()
        except OSError:
            try:
                api.get("dosbox/info")
            except OSError:
                print(tr("prototype_closed"), flush=True)
                return {"user_closed": True, "session": session.reports}
            raise
        print(tr("prototype_ready", path=snapshot), flush=True)
        if args.smoke:
            if not snapshot.exists():
                observe(api, transport, language, level, True, step_door=True)
                api.put(str(session.ds + 0x2155 + 0x3f), b"\x02")
                assert session.request() == "save"
                session.save(snapshot)
                session.save(snapshot)
                assert snapshot.with_suffix(".PRV").exists()
            else:
                api.put(str(session.ds + 0x2155 + 0x43), b"\x02")
                assert session.request() == "load"
                session.load(snapshot)
                observe(api, transport, language, level)
            memory = Memory(api.get("memory/0/0x2000000"), session.ldt)
            api.game_base = session.ds
            frame_hash = capture(api, {"buffer_base": memory.pointer(session.ds + 0xb53a, 1)},
                                 directory / "smoke-frame.bmp")
            return {"session": session.reports, "prototype_smoke": True, "frame_sha256": frame_hash}
        while True:
            operation = None
            try:
                operation = session.request()
                if operation == "death":
                    session.choose_level_action(death=True)
                elif operation == "save":
                    print(tr("prototype_saving"), flush=True)
                    result = session.save(snapshot)
                    print(tr("prototype_saved"), flush=True)
                elif operation == "load":
                    print(tr("prototype_loading"), flush=True)
                    result = session.load(snapshot)
                    print(tr("prototype_loaded"), flush=True)
                if operation in ("save", "load"):
                    seconds = f"{result['elapsed_seconds']:.2f}"
                    if args.lang == "ru":
                        seconds = seconds.replace(".", ",")
                    print(tr("prototype_duration", seconds=seconds), flush=True)
                time.sleep(.05)
            except DeathChoicePending:
                session.choose_level_action(death=True)
            except OSError as exc:
                # File errors (including F9 before the first save) do not mean
                # that the game was closed. Keep the session alive if its API is.
                try:
                    api.get("dosbox/info")
                except OSError:
                    print(tr("prototype_closed"), flush=True)
                    return {"user_closed": True, "session": session.reports}
                session.reports.append({"failed_operation": operation, "error": repr(exc)})
                traceback.print_exc()
                print(tr("prototype_error", detail=str(exc)), flush=True)
            except ValueError as exc:
                session.reports.append({"failed_operation": operation, "error": repr(exc)})
                traceback.print_exc()
                print(tr("prototype_error", detail=str(exc)), flush=True)
    with session_log(directory / "session.log"):
        print(tr("prototype_log", path=directory / "session.log"), flush=True)
        trial("ru", args.nasm, observer=observer, world_research=True,
              interactive=not args.smoke, prebuilt_payload=payload, play_mode=not args.smoke)


if __name__ == "__main__":
    main()
