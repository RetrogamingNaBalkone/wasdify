"""RUS level-1 logical-state research, not a registered save/load patch.

The field inventory comes from 1068's native object methods and 1098's map
accessors. This adapter deliberately excludes native headers, vtables, resource
pixels and padding. Resource addresses are fresh bindings built before play;
only their logical keys and object IDs may enter a snapshot. The inventory is
experimental until full restart and gameplay acceptance have passed.
"""
from dataclasses import dataclass
import struct
from typing import Mapping

from .kremlin_save import SaveFormatError
from .kremlin_save_events import NativeObject, NativeWrite
from . import kremlin_save_doors as door_adapter


def require(value, code):
    if not value:
        raise SaveFormatError(code)


# Each entry is an independently accessed scalar, not a copied native region.
# Native 1068:15F1/09C5/0A85: coordinates, turn/move rates and inertia.
POSE = {
    "x": (0x26, "i"), "y": (0x2a, "i"), "z": (0x2e, "i"),
    "heading": (0x32, "h"), "turn_rate": (0x34, "h"), "motion_mode": (0x36, "h"),
    "move_rate": (0x38, "i"), "step_distance": (0x3c, "i"),
}
ACTOR = {
    **POSE, "commands": (0x1c, "I"), "layer": (0x5a, "h"),
    "map_glyph": (0x5c, "B"), "previous_glyph": (0x5d, "B"),
    "turn_step": (0x7c, "h"), "move_step": (0x7e, "h"),
    "motion_ready": (0x80, "h"), "turn_direction": (0x82, "h"),
    "motion_aux": (0x84, "h"), "move_accumulator": (0x90, "i"),
    "turn_accumulator": (0x94, "i"), "acceleration_sign": (0x98, "h"),
    "previous_commands": (0x9a, "I"), "height_min": (0x9e, "h"), "height_max": (0xa0, "h"),
}
PLAYER = {
    **ACTOR, "health": (0x5e, "h"), "energy": (0x60, "h"),
    "health_max": (0x62, "h"), "energy_max": (0x64, "h"),
    **{f"inventory_{i}": (0x66 + i * 2, "h") for i in range(6)},
    "keys": (0x72, "H"), "height_a": (0x76, "h"), "height_b": (0x78, "h"),
    "height_c": (0x7a, "h"), "selected_weapon": (0xce, "h"),
    "pending_weapon": (0xd0, "h"), "lives": (0xd6, "h"), "score": (0xd8, "h"),
}
# 1068:3F80/3747/3845: state, health, burst/cooldown and movement counters.
AI = {
    **ACTOR, "ai_state": (0xa2, "h"), "ai_health": (0xa4, "h"),
    "burst_limit": (0xa6, "h"), "cooldown": (0xa8, "h"), "burst_count": (0xaa, "h"),
    "stride_count": (0xac, "h"), "stride_limit": (0xae, "h"),
    "sight_distance": (0xba, "i"), "ai_aux": (0xe6, "B"),
}
PROJECTILE = {
    **POSE, "sprite_x": (0x54, "h"), "sprite_y": (0x56, "h"),
    "impact_state": (0x58, "h"), "projectile_rate": (0x5a, "h"),
    "damage": (0x5c, "h"), "damage_target": (0x5e, "h"), "map_glyph": (0x68, "B"),
}
FIELDS = {
    0: {}, 1: PLAYER, 2: {}, 3: {}, 4: {}, 5: ACTOR,
    6: {"switch_a": (0x54, "h"), "switch_b": (0x56, "h"), "switch_c": (0x58, "h")},
    7: {}, 8: PROJECTILE, 9: AI,
    10: {"weapon_state": (0x83, "h"), "weapon_step": (0x85, "h")},
    # 1068:06D6/0741/07A1: the floor effect has no animation resource,
    # but its crop phase and scheduled frame interval are mutable.
    12: {"frame": (0x50, "h"), "frame_interval": (0x52, "h")}, 13: {},
}
COMMON = {"flags": (0x15, "H"), "width": (0x17, "h"),
          "height": (0x19, "h"), "marker": (0x1b, "B")}
ANIMATION = {"animation_x": (0x20, "h"), "animation_y": (0x22, "h"),
             "animation_budget": (0x24, "h"), "frame": (0x50, "h"), "frame_interval": (0x52, "h")}
TIMERS = {1: (0x86,), 5: (0x86,), 8: (0x6d,), 9: (0x86, 0xb0)}
MAP_OFFSETS = (0x608a, 0x718e, 0x8292, 0x9396)
IMAGE_TRANSFORM_MASK = 0x0cc0  # 10F8:0592/05C9: fractional crop on either axis


@dataclass(frozen=True)
class Resource:
    pointer: bytes
    address: int


@dataclass(frozen=True)
class Resources:
    baseline: Mapping[int, NativeObject]
    images: Mapping[str, Resource]
    animations: Mapping[str, Resource]
    placed_actors: frozenset[int] | None = None

    def image_key(self, pointer):
        if pointer == bytes(4):
            return None
        keys = sorted(key for key, resource in self.images.items() if resource.pointer == pointer)
        require(bool(keys), "unbound_image")
        return keys[0]

    def animation_key(self, pointer):
        if pointer == bytes(4):
            return None
        keys = sorted(key for key, resource in self.animations.items() if resource.pointer == pointer)
        require(bool(keys), "unbound_animation")
        return keys[0]


def bind_resources(read, pointer_at, objects, *, placed_actors=None):
    """Run once on a fresh, stopped level, before pickups or combat."""
    images, animations = {}, {}

    def image(key, at):
        pointer = read(at, 4)
        if pointer != bytes(4):
            images[key] = Resource(pointer, pointer_at(at, 18))

    for key, obj in sorted(objects.items()):
        require(obj.native_type in FIELDS, "unknown_native_class")
        image(f"{key}/image", obj.address + 0x44)
        if obj.native_type == 0:
            for offset in (0x56, 0x5a, 0x5e, 0x62):
                image(f"{key}/face/{offset}", obj.address + offset)
        offsets = (0x48,)
        if obj.native_type == 8:
            offsets += (0x77, 0x7b)
        elif obj.native_type == 9:
            offsets += (0xe8, 0xec)
        for offset in offsets:
            at = obj.address + offset
            pointer = read(at, 4)
            if pointer == bytes(4):
                continue
            name = f"{key}/animation/{offset}"
            address = pointer_at(at, 8)
            count, = struct.unpack("<H", read(address, 2))
            require(0 < count <= 4096, "animation_frames")
            frames = pointer_at(address + 4, count * 10)
            animations[name] = Resource(pointer, address)
            for frame in range(count):
                image(f"{name}/frame/{frame}", frames + frame * 10 + 6)
    return Resources(dict(objects), images, animations, placed_actors)


def field_layout(obj, resources, *, spawned=False, active=False):
    require(obj.native_type in FIELDS, "unknown_native_class")
    fields = dict(COMMON)
    if obj.native_type == 7:
        # Flags/marker/phase are owned by the existing door adapter.
        fields = {name: item for name, item in fields.items() if name in ("width", "height")}
    elif any(key.startswith(f"{obj.object_id}/animation/72") for key in resources.animations):
        fields.update(ANIMATION)
    elif obj.native_type == 8:
        fields.update(ANIMATION)
    fields.update(FIELDS[obj.native_type])
    if obj.native_type == 8 and obj.object_id in resources.baseline and not spawned:
        # Static projectile definitions are templates, not spawned actors.
        # Their position/owner/impact-animation slots have no native initializer
        # and are not read until the clone + launch path supplies those values.
        for name in POSE:
            fields.pop(name, None)
    if (obj.native_type in (1, 5, 9) and resources.placed_actors is not None
            and obj.object_id not in resources.placed_actors and not active):
        # The resource directory contains unused actor definitions. 1098:0EF7
        # initializes position/bounds only for actors occurring in the wall map;
        # 1068:3747 initializes AI burst/stride counters only on activation.
        for name in (*POSE, "previous_commands", "height_min", "height_max", "burst_count", "stride_count"):
            fields.pop(name, None)
    return fields


def capture_fields(read, obj, resources, *, spawned=False):
    active = obj.native_type == 9 and struct.unpack("<h", read(obj.address + 0xa2, 2))[0] != 0
    values = {name: struct.unpack("<" + wire, read(obj.address + offset, struct.calcsize(wire)))[0]
              for name, (offset, wire) in field_layout(obj, resources, spawned=spawned, active=active).items()}
    if obj.native_type == 1:
        values["commands"] = 0  # pending human input is not continued after load
    return values


def capture_timer(read, at, wall_ticks):
    active, origin, elapsed = struct.unpack("<HII", read(at, 10))
    require(active in (0, 1), "timer_active")
    return {"active": active, "elapsed": (wall_ticks - origin) & 0xffffffff if active else elapsed}


def object_reference(read, at, objects):
    pointer = read(at, 4)
    if pointer == bytes(4):
        return 0
    matches = [key for key, obj in objects.items() if obj.pointer == pointer]
    require(len(matches) == 1, "unbound_object")
    return matches[0]


def projectile_template(read, obj, resources, objects):
    signature = tuple(read(obj.address + offset, 4) for offset in (0x48, 0x77, 0x7b))
    matches = [key for key, candidate in resources.baseline.items() if candidate.native_type == 8
               and key != obj.object_id and key in objects
               and objects[key].native_type == 8
               and objects[key].pointer == candidate.pointer
               and not struct.unpack("<H", read(candidate.address + 0x15, 2))[0] & 0x1400
               and signature == tuple(resources.animations[f"{key}/animation/{offset}"].pointer
                   if f"{key}/animation/{offset}" in resources.animations else bytes(4)
                   for offset in (0x48, 0x77, 0x7b))]
    require(bool(matches), "unbound_projectile_template")
    return min(matches)


def capture(read, objects, resources, ds, world, wall_ticks):
    width, = struct.unpack("<H", read(world, 2))
    height, = struct.unpack("<H", read(world + 4, 2))
    require((width, height) == (64, 64), "research_level_dimensions")
    result = {"objects": [], "maps": [read(ds + offset, width * height) for offset in MAP_OFFSETS],
              "world": {"completed": struct.unpack("<H", read(world + 0xc, 2))[0],
                        "projectiles": struct.unpack("<H", read(world + 0x73c, 2))[0]},
              "image_fractions": {}}
    for key, obj in sorted(objects.items()):
        baseline = resources.baseline.get(key)
        template = 0
        projectile_instance = (obj.native_type == 8 and
            struct.unpack("<H", read(obj.address + 0x15, 2))[0] & 0x1400)
        if baseline is None or baseline.pointer != obj.pointer or projectile_instance:
            require(obj.native_type == 8, "unsupported_dynamic_class")
            template = projectile_template(read, obj, resources, objects)
        require(baseline is None or template or baseline.native_type == obj.native_type, "native_class_changed")
        if (obj.native_type == 9 and resources.placed_actors is not None
                and key not in resources.placed_actors
                and struct.unpack("<h", read(obj.address + 0xa2, 2))[0] == 0):
            require(struct.unpack("<H", read(obj.address, 2))[0] == 0, "inactive_unplaced_actor_events")
        record = {"id": key, "type": obj.native_type, "template": template,
                  "fields": capture_fields(read, obj, resources, spawned=bool(template)),
                  "image": resources.image_key(read(obj.address + 0x44, 4)),
                  "timers": [capture_timer(read, obj.address + offset, wall_ticks)
                             for offset in TIMERS.get(obj.native_type, ())], "refs": {}}
        if obj.native_type == 1:
            record["refs"] = {"weapons": [object_reference(read, obj.address + 0xaa + slot * 4, objects)
                                          for slot in range(9)],
                              "previous_tile": object_reference(read, obj.address + 0xda, objects)}
        elif obj.native_type == 8:
            prototype = key in resources.baseline and not template
            require(not prototype or not record["fields"]["flags"] & 0x1000, "active_projectile_template")
            record["refs"] = {"owner": 0 if prototype else object_reference(read, obj.address + 0x69, objects)}
            record["impact_animation"] = (resources.animation_key(read(obj.address + 0x7f, 4))
                                           if record["fields"]["impact_state"] else None)
        result["objects"].append(record)
    # Keep only canonical image keys, so shared native descriptors get one write.
    door_addresses = {obj.address + 0x6c for obj in objects.values() if obj.native_type == 7}
    for key, resource in sorted(resources.images.items()):
        if key == resources.image_key(resource.pointer) and resource.address not in door_addresses:
            flags, denominator, numerator = struct.unpack("<Hhh", read(resource.address + 4, 6))
            transform = flags & IMAGE_TRANSFORM_MASK
            # Fraction words in untransformed file resources are not runtime
            # state and may be zero. Do not persist or interpret those bytes.
            result["image_fractions"][key] = [transform, denominator, numerator] if transform else [0, 1, 0]
    return result


def construction_plan(state, resources):
    """Validate the complete instance inventory before any native deletion."""
    records = state["objects"]
    by_id = {record["id"]: record for record in records}
    require(len(by_id) == len(records), "duplicate_object")
    require(all(type(key) is int and 1 <= key <= 256 for key in by_id), "state_object_id")
    remove = []
    for key, baseline in resources.baseline.items():
        record = by_id.get(key)
        if record is None or record["template"]:
            # Only native projectiles have a confirmed reconstruction/destructor
            # contract. Never generalize this to doors, actors or game resources.
            require(baseline.native_type == 8, "snapshot_baseline_missing")
            remove.append(key)
        else:
            require(record["type"] == baseline.native_type, "native_class_changed")
    clones = []
    for record in records:
        key, template = record["id"], record["template"]
        if template:
            require(type(template) is int and template in resources.baseline
                    and resources.baseline[template].native_type == 8 and record["type"] == 8,
                    "projectile_template")
            require(template in by_id and not by_id[template]["template"] and template not in remove,
                    "projectile_template_lifetime")
            clones.append((key, template))
        else:
            require(key in resources.baseline, "projectile_template")
    return tuple(remove), tuple(clones)


def plan_restore(state, read, objects, resources, ds, world, wall_ticks):
    """Plan scalar/ID/resource rebinding after native instance construction."""
    require(set(state) == {"objects", "maps", "world", "image_fractions"}, "state_sections")
    require(all(type(record["id"]) is int and 1 <= record["id"] <= 256
                and type(record["type"]) is int and record["type"] in FIELDS
                and type(record["template"]) is int and 0 <= record["template"] <= 256
                for record in state["objects"]), "state_object_id")
    require({record["id"] for record in state["objects"]} == set(objects), "state_object_inventory")
    require(len(state["objects"]) == len(objects), "duplicate_object")
    writes = []

    def write(at, raw):
        expected = read(at, len(raw))
        require(len(expected) == len(raw), "native_range")
        writes.append(NativeWrite(at, expected, raw))

    def number(wire, value):
        require(type(value) is int, "scalar_value")
        try:
            return struct.pack("<" + wire, value)
        except struct.error as exc:
            raise SaveFormatError("scalar_range") from exc

    def ref(key, required_type=None):
        if key == 0:
            return bytes(4)
        require(type(key) is int and key in objects, "object_reference")
        require(required_type is None or objects[key].native_type == required_type, "reference_type")
        return objects[key].pointer

    for record in state["objects"]:
        obj = objects[record["id"]]
        keys = {"id", "type", "template", "fields", "image", "timers", "refs"}
        if obj.native_type == 8:
            keys.add("impact_animation")
        require(set(record) == keys, "state_record_fields")
        require(not record["template"] or (obj.native_type == 8
                and record["template"] in resources.baseline
                and resources.baseline[record["template"]].native_type == 8), "projectile_template")
        require(record["type"] == obj.native_type, "native_class_changed")
        values = record["fields"]
        layout = field_layout(obj, resources, spawned=bool(record["template"]),
                              active=obj.native_type == 9 and values.get("ai_state", 0) != 0)
        require(set(values) == set(layout), "state_fields")
        if obj.native_type == 9:
            require(values["ai_state"] in range(5), "ai_state")
        if obj.native_type == 12:
            require(0 <= values["frame"] <= 18 and values["frame_interval"] >= 0, "floor_effect")
        if "heading" in values:
            require(0 <= values["heading"] < 360, "heading")
            if not (0 <= values["x"] < 64 * 480 and 0 <= values["z"] < 64 * 480):
                error = SaveFormatError("coordinates")
                error.add_note(f"object_id={obj.object_id}, native_type={obj.native_type}, "
                               f"x={values['x']}, z={values['z']}")
                raise error
        if obj.native_type == 1:
            require(0 < values["health"] <= values["health_max"] and values["lives"] > 0, "player_state")
            require(0 <= values["energy"] <= values["energy_max"], "player_state")
            require(0 <= values["selected_weapon"] <= 8 and 0 <= values["pending_weapon"] <= 8,
                    "weapon_slot")
        for name, (offset, wire) in layout.items():
            write(obj.address + offset, number(wire, values[name]))
        image = record["image"]
        require(image is None or image in resources.images, "image_reference")
        if obj.native_type != 7:  # self-image pointer belongs to the fresh door
            write(obj.address + 0x44, bytes(4) if image is None else resources.images[image].pointer)
        timers = TIMERS.get(obj.native_type, ())
        require(len(record["timers"]) == len(timers), "state_timers")
        for offset, timer in zip(timers, record["timers"]):
            require(set(timer) == {"active", "elapsed"}, "timer_fields")
            active, elapsed = timer["active"], timer["elapsed"]
            require(active in (0, 1) and type(elapsed) is int and 0 <= elapsed <= 0xffffffff, "timer_value")
            write(obj.address + offset, struct.pack("<HII", active, (wall_ticks - elapsed) & 0xffffffff, elapsed))
        if obj.native_type == 1:
            require(set(record["refs"]) == {"weapons", "previous_tile"}, "state_references")
            weapons = record["refs"]["weapons"]
            require(len(weapons) == 9, "weapon_slots")
            require(not values["selected_weapon"] or weapons[values["selected_weapon"]], "selected_weapon")
            require(not values["pending_weapon"] or weapons[values["pending_weapon"]], "selected_weapon")
            for slot, key in enumerate(weapons):
                pointer = ref(key, 10)
                if key:
                    require(struct.unpack("<h", read(objects[key].address + 0x81, 2))[0] == slot,
                            "weapon_binding")
                write(obj.address + 0xaa + slot * 4, pointer)
            write(obj.address + 0xda, ref(record["refs"]["previous_tile"]))
        elif obj.native_type == 8:
            require(set(record["refs"]) == {"owner"}, "state_references")
            owner = record["refs"]["owner"]
            require(not owner or owner in objects and objects[owner].native_type in (1, 9), "projectile_owner")
            write(obj.address + 0x69, ref(record["refs"]["owner"]))
            animation = record["impact_animation"]
            require(animation is None or animation in resources.animations, "animation_reference")
            write(obj.address + 0x7f, bytes(4) if animation is None else resources.animations[animation].pointer)
        else:
            require(not record["refs"], "state_references")
    require(len(state["maps"]) == 4, "map_count")
    for offset, layer in zip(MAP_OFFSETS, state["maps"]):
        require(type(layer) is bytes and len(layer) == 4096, "map_size")
        if offset != 0x9396:
            require(all(value + 1 in objects for value in layer), "map_object_reference")
        write(ds + offset, layer)
    require(set(state["world"]) == {"completed", "projectiles"}, "world_fields")
    for name, offset in (("completed", 0xc), ("projectiles", 0x73c)):
        write(world + offset, number("H", state["world"][name]))
    # Door fractions are reconstructed by the dedicated native door contract.
    door_addresses = {obj.address + 0x6c for obj in objects.values() if obj.native_type == 7}
    canonical = {key for key, resource in resources.images.items()
                 if key == resources.image_key(resource.pointer) and resource.address not in door_addresses}
    require(set(state["image_fractions"]) == canonical, "image_inventory")
    for key, fraction in state["image_fractions"].items():
        resource = resources.images[key]
        require(len(fraction) == 3 and all(type(value) is int for value in fraction), "image_fraction")
        transform, denominator, numerator = fraction
        require(0 <= transform <= 0xffff and transform & ~IMAGE_TRANSFORM_MASK == 0, "image_fraction")
        require(1 <= denominator <= 0x7fff and 0 <= numerator <= denominator, "image_fraction")
        if resource.address not in door_addresses:
            flags, = struct.unpack("<H", read(resource.address + 4, 2))
            write(resource.address + 4, struct.pack("<Hhh", flags & ~IMAGE_TRANSFORM_MASK | transform,
                                                   denominator, numerator))
    return tuple(writes)


def plan_hud(state, read, interface):
    """Rebuild 10C0:1395…1497's cached HUD fields, including cleared keys."""
    heroes = [record for record in state["objects"] if record["type"] == 1]
    require(len(heroes) == 1, "player_count")
    hero = heroes[0]
    fields = hero["fields"]
    weapon_mask = sum(1 << (slot - 1) for slot, key in enumerate(hero["refs"]["weapons"]) if slot and key)
    values = ((0x12, fields["health"]), (0x14, fields["health_max"]),
              (0x16, fields["energy"]), (0x18, fields["energy_max"]),
              (0x1a, fields["keys"]), (0x22, fields["score"]), (0x26, weapon_mask))
    result = [NativeWrite(interface + offset, read(interface + offset, 2), struct.pack("<H", value & 0xffff))
              for offset, value in values]
    flags, = struct.unpack("<H", read(interface + 0x10, 2))
    result.append(NativeWrite(interface + 0x10, read(interface + 0x10, 2), struct.pack("<H", flags | 0x61e)))
    return tuple(result)
