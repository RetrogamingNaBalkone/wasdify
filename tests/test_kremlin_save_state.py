"""State research uses invented objects/maps/resources, never game files."""
import copy
import struct
import unittest

from wasdify import kremlin_save_state as state
from wasdify.kremlin_save import SaveFormatError
from wasdify.kremlin_save_events import NativeObject
from dataclasses import replace


class NativeMemory:
    def __init__(self, relocation=0):
        self.data = bytearray(0x40000)
        self.ds = 0x20000 + relocation
        self.world = 0x1a000 + relocation
        self.objects = {}
        for key, kind in ((1, 1), (2, 8), (33, 0), (50, 8), (88, 10)):
            address = 0x1000 + key * 0x100 + relocation
            self.objects[key] = NativeObject(key, kind, address, struct.pack("<I", address),
                                             0x10000 + key * 64 + relocation, 4)
        images = {}
        for key in (1, 33, 50, 88):
            address = 0xc000 + key * 32 + relocation
            images[f"{key}/image"] = state.Resource(struct.pack("<I", address), address)
            struct.pack_into("<hh", self.data, address + 6, 1, 1)
        self.resources = state.Resources({k: o for k, o in self.objects.items() if k != 2}, images, {})
        for key, obj in self.objects.items():
            self.write(obj.address + 0x10, b"vt")
            self.write(obj.address + 0x12, struct.pack("<H", obj.native_type))
            resource = images[f"{50 if key == 2 else key}/image"]
            self.write(obj.address + 0x44, resource.pointer)
            for _, (offset, wire) in state.field_layout(obj, self.resources).items():
                self.write(obj.address + offset, struct.pack("<" + wire, 0))
        hero = self.objects[1].address
        for name, value in {"health": 80, "health_max": 100, "energy": 40, "energy_max": 100,
                            "lives": 3, "selected_weapon": 1, "pending_weapon": 1,
                            "x": 1000, "z": 1500, "heading": 90}.items():
            offset, wire = state.PLAYER[name]
            self.write(hero + offset, struct.pack("<" + wire, value))
        self.write(hero + 0xae, self.objects[88].pointer)
        self.write(hero + 0xda, self.objects[33].pointer)
        self.write(self.objects[88].address + 0x81, struct.pack("<h", 1))
        self.write(self.objects[2].address + 0x69, self.objects[1].pointer)
        self.write(self.objects[2].address + 0x7f, b"junk")  # unused outside impact state
        self.write(self.objects[2].address + 0x15, struct.pack("<H", 0x1000))
        self.write(hero + 0x86, struct.pack("<HII", 1, 0xfffffff0, 10))
        struct.pack_into("<H", self.data, self.world, 64)
        struct.pack_into("<H", self.data, self.world + 4, 64)
        struct.pack_into("<H", self.data, self.world + 0x73c, 1)
        for offset in state.MAP_OFFSETS[:3]:
            self.write(self.ds + offset, bytes((32,)) * 4096)

    def read(self, at, size):
        return bytes(self.data[at:at + size])

    def write(self, at, raw):
        self.data[at:at + len(raw)] = raw

    def capture(self, now=16):
        return state.capture(self.read, self.objects, self.resources, self.ds, self.world, now)

    def plan(self, snapshot, now=100000):
        return state.plan_restore(snapshot, self.read, self.objects, self.resources,
                                  self.ds, self.world, now)

    def apply(self, plan):
        for write in plan:
            assert self.read(write.address, len(write.expected)) == write.expected
        for write in plan:
            self.write(write.address, write.replacement)


class WorldStateTests(unittest.TestCase):
    def test_reused_projectile_slot_is_an_instance_even_when_allocator_reuses_address(self):
        native = NativeMemory()
        # Slot 2 was already occupied at resource binding, then the native
        # allocator destroyed and reused that allocation. Pointer equality is
        # insufficient to classify this active shot as a resource definition.
        native.resources = replace(native.resources,
            baseline={**native.resources.baseline, 2: native.objects[2]})
        snapshot = native.capture()
        shot = next(item for item in snapshot["objects"] if item["id"] == 2)
        self.assertEqual(shot["template"], 50)
        self.assertIn("x", shot["fields"])
        self.assertEqual(state.construction_plan(snapshot, native.resources), ((2,), ((2, 50),)))
        native.plan(snapshot)
        del native.objects[2]
        absent = native.capture()
        self.assertEqual(state.construction_plan(absent, native.resources), ((2,), ()))

    def test_construction_inventory_rejects_cycles_missing_templates_and_static_removal(self):
        native = NativeMemory()
        snapshot = native.capture()
        state.construction_plan(snapshot, native.resources)
        missing = copy.deepcopy(snapshot)
        missing["objects"] = [r for r in missing["objects"] if r["id"] != 1]
        with self.assertRaisesRegex(SaveFormatError, "snapshot_baseline_missing"):
            state.construction_plan(missing, native.resources)
        cyclic = copy.deepcopy(snapshot)
        next(r for r in cyclic["objects"] if r["id"] == 50)["template"] = 50
        with self.assertRaisesRegex(SaveFormatError, "projectile_template_lifetime"):
            state.construction_plan(cyclic, native.resources)
        unavailable = copy.deepcopy(snapshot)
        unavailable["objects"] = [r for r in unavailable["objects"] if r["id"] != 50]
        with self.assertRaisesRegex(SaveFormatError, "projectile_template_lifetime"):
            state.construction_plan(unavailable, native.resources)

    def test_hud_restores_values_clears_keys_and_marks_native_redraw(self):
        native = NativeMemory()
        snapshot = native.capture()
        interface = 0x18000
        native.write(interface + 0x10, struct.pack("<H", 0x8000))
        native.write(interface + 0x1a, struct.pack("<H", 7))
        before = bytes(native.data)
        plan = state.plan_hud(snapshot, native.read, interface)
        self.assertEqual(bytes(native.data), before)
        native.apply(plan)
        self.assertEqual(struct.unpack("<5H", native.read(interface + 0x12, 10)), (80, 100, 40, 100, 0))
        self.assertEqual(struct.unpack("<H", native.read(interface + 0x26, 2))[0], 1)
        self.assertEqual(struct.unpack("<H", native.read(interface + 0x10, 2))[0], 0x861e)

    def test_relocates_inventory_owner_images_and_maps_without_copying_native_headers(self):
        old, fresh = NativeMemory(), NativeMemory(0x2000)
        snapshot = old.capture()
        fresh.write(fresh.objects[1].address + 0xaa, bytes(36))
        fresh.write(fresh.objects[1].address + 0x5e, struct.pack("<h", 1))
        before = bytes(fresh.data)
        plan = fresh.plan(snapshot)
        self.assertEqual(bytes(fresh.data), before)
        fresh.apply(plan)
        self.assertEqual(fresh.capture(100000), snapshot)
        self.assertEqual(fresh.read(fresh.objects[1].address + 0xae, 4), fresh.objects[88].pointer)
        self.assertEqual(fresh.read(fresh.objects[2].address + 0x69, 4), fresh.objects[1].pointer)
        for obj in fresh.objects.values():
            self.assertEqual(fresh.read(obj.address + 0x10, 2), b"vt")
            self.assertEqual(fresh.read(obj.address, 16), before[obj.address:obj.address + 16])

    def test_timer_retains_elapsed_duration_across_u32_wrap_and_new_wall_clock(self):
        native = NativeMemory()
        timer = state.capture_timer(native.read, native.objects[1].address + 0x86, 16)
        self.assertEqual(timer, {"active": 1, "elapsed": 32})
        snapshot = native.capture()
        native.apply(native.plan(snapshot, now=8))
        self.assertEqual(state.capture_timer(native.read, native.objects[1].address + 0x86, 8), timer)

    def test_unknown_fields_dangling_ids_bad_maps_and_values_produce_no_plan(self):
        native = NativeMemory()
        original = native.capture()
        mutations = (
            lambda s: s["objects"][0]["fields"].update(extra=1),
            lambda s: s["objects"][0]["fields"].update(health=101),
            lambda s: s["objects"][0]["fields"].update(heading=360),
            lambda s: s["objects"][0]["fields"].update(x=-1),
            lambda s: s["objects"][0].update(image="unknown"),
            lambda s: s["objects"][0]["refs"]["weapons"].__setitem__(1, 999),
            lambda s: s["objects"][0]["refs"]["weapons"].__setitem__(1, 33),
            lambda s: s["maps"].__setitem__(0, b"\xff" * 4096),
            lambda s: s["maps"].__setitem__(0, b"short"),
            lambda s: s["image_fractions"].__setitem__("1/image", [0, 0]),
            lambda s: s["objects"].append(s["objects"][0]),
        )
        before = bytes(native.data)
        for change in mutations:
            snapshot = copy.deepcopy(original)
            change(snapshot)
            with self.subTest(change=change), self.assertRaises(SaveFormatError):
                native.plan(snapshot)
            self.assertEqual(bytes(native.data), before)

    def test_unused_projectile_slots_are_not_interpreted_as_persisted_pointers(self):
        native = NativeMemory()
        template = native.objects[50]
        native.write(template.address + 0x69, b"junk")
        native.write(template.address + 0x26, b"junkjunkjunk")
        snapshot = native.capture()
        record = next(item for item in snapshot["objects"] if item["id"] == 50)
        self.assertEqual(record["refs"]["owner"], 0)
        self.assertNotIn("x", record["fields"])
        native.plan(snapshot)

    def test_fractional_image_state_restores_transform_bits_and_keeps_fresh_pixels(self):
        old, fresh = NativeMemory(), NativeMemory(0x2000)
        image = old.resources.images["1/image"]
        new_image = fresh.resources.images["1/image"]
        old.write(image.address + 4, struct.pack("<Hhh", 0x48, 18, 7))
        fresh.write(new_image.address + 4, struct.pack("<Hhh", 8, 0, 0))
        fresh.write(new_image.address + 14, b"new!")
        snapshot = old.capture()
        fresh.apply(fresh.plan(snapshot))
        self.assertEqual(fresh.read(new_image.address + 4, 6), struct.pack("<Hhh", 0x48, 18, 7))
        self.assertEqual(fresh.read(new_image.address + 14, 4), b"new!")
        self.assertEqual(fresh.capture(100000), snapshot)

    def test_unplaced_actor_definition_does_not_persist_uninitialized_position(self):
        native = NativeMemory()
        actor = NativeObject(3, 9, 0x1400, struct.pack("<I", 0x1400), 0x11000, 4)
        native.objects[3] = actor
        baseline = dict(native.resources.baseline)
        baseline[3] = actor
        native.resources = replace(native.resources, baseline=baseline, placed_actors=frozenset({1}))
        native.write(actor.address + 0x44, native.resources.images["1/image"].pointer)
        native.write(actor.address + 0x26, b"junkjunkjunk")
        snapshot = native.capture()
        record = next(item for item in snapshot["objects"] if item["id"] == 3)
        self.assertNotIn("x", record["fields"])
        self.assertNotIn("burst_count", record["fields"])
        native.plan(snapshot)
        native.write(actor.address + 0xa2, struct.pack("<h", 1))
        # Activation must no longer silently discard the now-live position.
        with self.assertRaisesRegex(SaveFormatError, "coordinates"):
            native.plan(native.capture())
        for name, value in {"x": 1200, "y": -40, "z": 2400, "heading": 90,
                            "burst_count": 2, "stride_count": 3}.items():
            offset, wire = state.AI[name]
            native.write(actor.address + offset, struct.pack("<" + wire, value))
        snapshot = native.capture()
        record = next(item for item in snapshot["objects"] if item["id"] == 3)
        self.assertEqual(record["fields"]["burst_count"], 2)
        native.write(actor.address + 0x26, struct.pack("<i", 5000))
        native.apply(native.plan(snapshot))
        self.assertEqual(native.capture(100000), snapshot)

    def test_floor_effect_phase_survives_resource_rebinding(self):
        old, fresh = NativeMemory(), NativeMemory(0x2000)
        for native in (old, fresh):
            obj = native.objects[33]
            native.objects[33] = replace(obj, native_type=12)
            native.resources = replace(native.resources,
                baseline={**native.resources.baseline, 33: native.objects[33]})
        old.write(old.objects[33].address + 0x50, struct.pack("<hh", 7, 150))
        snapshot = old.capture()
        fresh.apply(fresh.plan(snapshot))
        self.assertEqual(fresh.capture(100000), snapshot)
        legacy = copy.deepcopy(snapshot)
        record = next(item for item in legacy["objects"] if item["id"] == 33)
        del record["fields"]["frame"], record["fields"]["frame_interval"]
        normalized = state.with_floor_defaults(legacy)
        self.assertNotIn("frame", record["fields"])
        fresh.apply(fresh.plan(normalized))
        self.assertEqual(fresh.read(fresh.objects[33].address + 0x50, 4), struct.pack("<hh", 18, 0))
        for name, value in (("frame", 19), ("frame", -1), ("frame_interval", -1)):
            invalid = copy.deepcopy(snapshot)
            next(item for item in invalid["objects"] if item["id"] == 33)["fields"][name] = value
            with self.assertRaisesRegex(SaveFormatError, "floor_effect"):
                fresh.plan(invalid)


if __name__ == "__main__":
    unittest.main()
