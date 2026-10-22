"""Restart a real RUS level-1 process and restore doors plus the event queue.

Development experiment only: the JSON artifact is NOT a complete saved game or
the KRS format. Actors, inventory, maps, RNG and scripts are not restored. Both
processes independently perform the same native pickups to obtain matching
object inventories. No heap dump, pointer or executable code is persisted.
"""
import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path

from inspect_kremlin_save_world import observe
from profile_kremlin import ROOT
from verify_kremlin_save_io import PROFILES, trial
from verify_kremlin_settings_dosbox import text
from wasdify import kremlin_save_doors as door_adapter
from wasdify import kremlin_save_events as event_adapter
from wasdify.kremlin_save import EventRecord


def inventory(objects):
    return [{"id": key, "type": obj.native_type} for key, obj in sorted(objects.items())]


def door_ids(objects, doors):
    by_address = {obj.address: key for key, obj in objects.items()}
    return {by_address[binding.address]: (binding, state) for binding, state in doors}


def logical(objects, events, doors):
    return {"objects": inventory(objects), "events": [asdict(event) for event in events],
            "doors": [{"id": key, "state": asdict(state)}
                      for key, (_, state) in sorted(door_ids(objects, doors).items())]}


def bindings(objects, callbacks):
    # Diagnostics only, kept separate from the portable component artifact.
    return {"objects": {str(key): {"address": obj.address, "pointer": obj.pointer.hex(),
                                  "events_address": obj.events_address}
                        for key, obj in objects.items()},
            "callbacks": {str(key): value.hex() for key, value in callbacks.items()}}


def resource_hashes():
    folder = ROOT / "originals/games/pkr/RUS"
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def run(nasm):
    work = ROOT / "mqa" / ("save-restart-ru-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    work.mkdir(parents=True)
    artifact = work / "components.json"
    report = {"scope": "doors-and-event-queue-only", "full_game_save": False,
              "language": "ru", "level": 1, "status": "running"}
    resources = resource_hashes()
    expected_reference = PROFILES["ru"][4]
    if resources.get("PKR.EXE") != expected_reference:
        raise ValueError("Unexpected reference executable")

    def capture(memory, objects, queue, callbacks, now, doors):
        events = event_adapter.capture(memory.read, objects, queue, callbacks, now)
        if not any(not state.ready for _, state in doors):
            raise ValueError("Capture did not catch a naturally moving door")
        payload = {"experiment": "kremlin-components-v1", "resources": resources,
                   "state": logical(objects, events, doors)}
        raw = json.dumps(payload, sort_keys=True, indent=2).encode("ascii")
        with artifact.open("xb") as stream:
            stream.write(raw)
        report["artifact_sha256"] = hashlib.sha256(raw).hexdigest()
        report["source_bindings"] = bindings(objects, callbacks)
        return (), events, doors, {"captured": True, "moving_doors": sum(
            not state.ready for _, state in doors)}

    def restore(memory, objects, queue, callbacks, now, doors):
        raw = artifact.read_bytes()
        if hashlib.sha256(raw).hexdigest() != report["artifact_sha256"]:
            raise ValueError("Component artifact changed between processes")
        payload = json.loads(raw)
        if payload["experiment"] != "kremlin-components-v1" or payload["resources"] != resource_hashes():
            raise ValueError("Research resources changed between processes")
        state = payload["state"]
        if inventory(objects) != state["objects"]:
            raise ValueError("Fresh process has a different object inventory; construction is still required")
        events = tuple(EventRecord(item["event_id"], item["owner_id"], item["event_type"],
                                   item["event_version"], {int(k): v for k, v in item["fields"].items()})
                       for item in state["events"])
        current_events = event_adapter.capture(memory.read, objects, queue, callbacks, now)
        by_id = door_ids(objects, doors)
        if {item["id"] for item in state["doors"]} != set(by_id):
            raise ValueError("Fresh process has a different door inventory")
        restored_doors = [(by_id[item["id"]][0], door_adapter.DoorState(**item["state"]))
                          for item in state["doors"]]
        changed_doors = sum(door_adapter.capture(memory.read, binding) != saved
                            for binding, saved in restored_doors)
        if not changed_doors:
            raise ValueError("Restoration would only rewrite identical door state")
        target_bindings = bindings(objects, callbacks)
        old_bindings = report["source_bindings"]["objects"]
        moved = sum(value != old_bindings[key] for key, value in target_bindings["objects"].items())
        if not moved:
            raise ValueError("Fresh allocations did not move; relocation exercise was inconclusive")
        plan = event_adapter.plan_restore(events, memory.read, objects, queue, callbacks, now)
        for binding, saved in restored_doors:
            plan += door_adapter.plan_restore(saved, memory.read, binding)
        report["target_bindings"] = target_bindings
        return plan, events, restored_doors, {
            "restored": True, "changed_doors": changed_doors, "relocated_objects": moved,
            "queue_was_different": current_events != events,
            "moving_doors": sum(not saved.ready for _, saved in restored_doors)}

    def continued(memory, objects, queue, callbacks, now, doors):
        saved = json.loads(artifact.read_bytes())["state"]
        by_id = door_ids(objects, doors)
        moving = [item for item in saved["doors"] if not item["state"]["ready"]]
        advanced = [item["id"] for item in moving
                    if by_id[item["id"]][1].position != item["state"]["position"]]
        if not moving or len(advanced) != len(moving):
            raise ValueError("Restored door animation did not advance after resuming")
        events = event_adapter.capture(memory.read, objects, queue, callbacks, now)
        return (), events, doors, {
            "native_animation_advanced": advanced, "queue_valid": True,
            "door_states": [{"id": item["id"], "saved": item["state"],
                             "continued": asdict(by_id[item["id"]][1])} for item in moving]}

    try:
        source = trial("ru", nasm, observer=lambda api, base, language, level: observe(
            api, base, language, level, True, components=capture, step_door=True))
        report["source"] = source
        # trial waits for this process to exit before returning. The next trial
        # starts another DOSBox, loads the level normally and reads only JSON.
        def load_and_continue(api, base, language, level):
            loaded = observe(api, base, language, level, True, components=restore, settle_seconds=1.5)
            resumed = observe(api, base, language, level, components=continued)
            screen = text(api)
            if "EXCEPTION" in screen:
                raise ValueError("Native game exception after restoration")
            return {"loaded": loaded, "continued": resumed}
        target = trial("ru", nasm, observer=load_and_continue, data_size=12288)
        report["target"] = target
        if not source["reference_unchanged"] or not target["reference_unchanged"]:
            raise ValueError("Reference executable changed")
        if resources != resource_hashes():
            raise ValueError("Reference resources changed")
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        raise
    finally:
        (work / "report.json").write_text(json.dumps(report, indent=2), encoding="ascii")
        print(work, flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nasm", type=Path, default=ROOT / "build/tools/nasm-2.16.03/nasm.exe")
    run(parser.parse_args().nasm)
