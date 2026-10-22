"""Synthetic KRS reference-format checks; no game schema or game bytes."""
from dataclasses import replace
from hashlib import sha256
import struct
import unittest
from zlib import crc32

from wasdify import kremlin_save as krs


def any_value(value):
    return True


def synthetic_schema():
    reference = krs.FieldSpec(7, lambda n: n >= 0, reference_classes=frozenset({1}))
    scalar = krs.FieldSpec(2, lambda n: 0 <= n <= 100)
    record = krs.RecordSpec({1: scalar, 2: reference}, lambda f: f[1] <= 100)
    return krs.Schema(
        0x7ffffffe,
        krs.RecordSpec({1: reference}, lambda f: f[1] != 0),
        krs.RecordSpec({1: krs.FieldSpec(8, lambda b: len(b) == 4, max_size=4)}, any_value),
        krs.RecordSpec({1: krs.FieldSpec(3, any_value)}, any_value),
        {(1, 1): record}, {(1, 1): record},
        lambda s: bool(s.objects) and s.game[1] == s.objects[0].object_id,
    )


def synthetic_snapshot():
    return krs.Snapshot(1, 12345, bytes(32), {1: 1}, {1: b"abcd"},
                        (krs.ObjectRecord(1, 1, 1, 77, {1: 90, 2: 2}),
                         krs.ObjectRecord(2, 1, 1, 0xffff, {1: 50, 2: 1})),
                        (krs.EventRecord(1, 2, 1, 1, {1: 10, 2: 1}),), {1: 123})


def chunks(data):
    result, pos = [], 128
    while pos < len(data):
        tag, version, flags, size, checksum = krs.CHUNK.unpack_from(data, pos)
        result.append((tag, version, flags, data[pos + 16:pos + 16 + size]))
        pos += 16 + size
    return result


def rebuild(data, entries=None, header_changes=None):
    entries = chunks(data) if entries is None else entries
    body = b"".join(krs.CHUNK.pack(tag, version, flags, len(payload), crc32(payload)) + payload
                    for tag, version, flags, payload in entries)
    header = bytearray(data[:128])
    struct.pack_into("<I", header, 12, 128 + len(body))
    struct.pack_into("<II", header, 108, len(entries), crc32(body))
    for offset, replacement in (header_changes or {}).items():
        header[offset:offset + len(replacement)] = replacement
    struct.pack_into("<I", header, 116, 0)
    struct.pack_into("<I", header, 116, crc32(header))
    return bytes(header) + body


class SaveFormatTests(unittest.TestCase):
    def setUp(self):
        self.schema = synthetic_schema()
        self.compat = krs.Compatibility(1, 1, 1, sha256(b"synthetic resources").digest())
        self.snapshot = synthetic_snapshot()
        self.data = krs.encode(self.snapshot, self.schema, self.compat)

    def decode(self, data):
        return krs.decode(data, self.schema, self.compat)

    def rejects(self, data, code=None):
        with self.assertRaises(krs.SaveFormatError) as caught:
            self.decode(data)
        if code is not None:
            self.assertEqual(caught.exception.code, code)

    def test_round_trip_including_cyclic_references_and_order(self):
        self.assertEqual(self.decode(self.data), self.snapshot)
        self.assertEqual(krs.encode(self.decode(self.data), self.schema, self.compat), self.data)
        self.assertEqual(krs.HEADER.size, 128)
        self.assertEqual(crc32(b"123456789"), 0xcbf43926)
        self.assertEqual(self.data[:8], b"WASDKRS\0")
        self.assertEqual(struct.unpack_from("<Q", self.data, 36)[0], 12345)
        self.assertEqual(self.data[44:76], self.compat.content_sha256)
        self.assertEqual(self.data[76:108], self.snapshot.source_sha256)

    def test_every_truncation_and_single_byte_corruption(self):
        for length in range(len(self.data)):
            with self.subTest(length=length):
                self.rejects(self.data[:length])
        for position in range(len(self.data)):
            damaged = bytearray(self.data)
            damaged[position] ^= 1
            with self.subTest(position=position):
                self.rejects(bytes(damaged))

    def test_all_header_contracts_with_valid_crc(self):
        changes = {0: b"BADMAGIC", 8: b"\x02\0", 10: b"\x7f\0",
                   12: struct.pack("<I", 128), 16: bytes(4), 20: bytes(4),
                   24: b"\0", 25: b"\x03", 26: b"\x01\0", 28: bytes(8),
                   44: bytes(32), 108: bytes(4), 120: b"\x01"}
        for offset, value in changes.items():
            with self.subTest(offset=offset):
                self.rejects(rebuild(self.data, header_changes={offset: value}))

    def test_compatibility_checks_all_dimensions_except_diagnostic_source(self):
        for change in ({"compat_id": 2}, {"level_id": 2}, {"language_id": 2},
                       {"content_sha256": bytes(32)}):
            with self.subTest(change=change), self.assertRaises(krs.SaveFormatError):
                krs.decode(self.data, self.schema, replace(self.compat, **change))
        altered = rebuild(self.data, header_changes={76: b"x" * 32})
        self.assertEqual(self.decode(altered).source_sha256, b"x" * 32)

    def test_optional_extensions_checked_and_required_unknown_rejected(self):
        entries = chunks(self.data)
        entries.insert(1, (b"XTRA", 99, 0, b"future"))
        self.assertEqual(self.decode(rebuild(self.data, entries)), self.snapshot)
        entries[1] = (b"XTRA", 1, 1, b"future")
        self.rejects(rebuild(self.data, entries), "unknown_required_chunk")
        entries[1] = (b"XTRA", 1, 2, b"future")
        self.rejects(rebuild(self.data, entries), "chunk_flags")

    def test_chunk_contracts(self):
        entries = chunks(self.data)
        self.rejects(rebuild(self.data, entries[:-1]), "missing_chunk")
        self.rejects(rebuild(self.data, entries + entries[:1]), "duplicate_chunk")
        self.rejects(rebuild(self.data, entries[1:] + entries[:1]), "chunk_order")
        for version, flags in ((2, 1), (1, 0)):
            changed = [(b"GAME", version, flags, entries[0][3])] + entries[1:]
            self.rejects(rebuild(self.data, changed), "chunk_version")
        self.rejects(self.data + b"garbage", "file_size")
        self.rejects(self.data + bytes(krs.MAX_FILE_SIZE), "file_size")

    def test_bounds_checked_even_with_valid_outer_checksums(self):
        entries = chunks(self.data)
        for tag_index in range(5):
            payload = entries[tag_index][3]
            for length in range(len(payload)):
                changed = entries.copy()
                changed[tag_index] = (*entries[tag_index][:3], payload[:length])
                with self.subTest(tag_index=tag_index, length=length):
                    self.rejects(rebuild(self.data, changed))
        for index, limit in ((2, krs.MAX_OBJECTS), (3, krs.MAX_EVENTS)):
            changed = entries.copy()
            changed[index] = (*entries[index][:3], struct.pack("<I", limit + 1))
            self.rejects(rebuild(self.data, changed))

    def test_fields_require_exact_type_size_order_and_semantics(self):
        entries = chunks(self.data)
        valid = entries[0][3]
        payloads = [valid + valid, bytes([0xff]) + valid[1:], b"",
                    krs.FIELD.pack(1, 3, 0, 4) + struct.pack("<I", 1),
                    krs.FIELD.pack(1, 7, 1, 4) + struct.pack("<I", 1),
                    krs.FIELD.pack(1, 7, 0, 3) + bytes(3),
                    krs.FIELD.pack(1, 7, 0, 0xffffffff),
                    krs.FIELD.pack(1, 7, 0, 4) + bytes(4)]
        for payload in payloads:
            changed = [(b"GAME", 1, 1, payload)] + entries[1:]
            self.rejects(rebuild(self.data, changed))

    def test_inner_chunk_crc_and_large_length_are_not_hidden_by_outer_crc(self):
        for relative, replacement, code in ((12, bytes(4), "chunk_crc"),
                                             (8, struct.pack("<I", 0xffffffff), "truncated")):
            damaged = bytearray(self.data)
            damaged[128 + relative:132 + relative] = replacement
            struct.pack_into("<I", damaged, 112, crc32(damaged[128:]))
            struct.pack_into("<I", damaged, 116, 0)
            struct.pack_into("<I", damaged, 116, crc32(damaged[:128]))
            self.rejects(bytes(damaged), code)

    def test_numeric_wire_types_and_explicit_byte_limits(self):
        values = {1: 255, 2: 65535, 3: 0xffffffff, 4: -32768,
                  5: -2147483648, 6: (1 << 64) - 1, 7: 0, 8: b"ok"}
        spec = krs.RecordSpec({wire: krs.FieldSpec(wire, any_value) for wire in values}, any_value)
        encoded = krs._encode_fields(values, spec)
        self.assertEqual(krs._decode_fields(encoded, spec), values)
        for wire in range(1, 8):
            with self.subTest(wire=wire), self.assertRaises(krs.SaveFormatError):
                krs._encode_fields({**values, wire: 1 << 65}, spec)
        with self.assertRaises(krs.SaveFormatError):
            krs._encode_fields({**values, 8: bytes(9)}, spec)

    def test_invalid_graphs_types_indices_and_ownership_rejected_on_both_paths(self):
        first, second = self.snapshot.objects
        variants = [replace(self.snapshot, objects=(first, first)),
                    replace(self.snapshot, objects=(replace(first, object_id=0), second)),
                    replace(self.snapshot, objects=(first, replace(second, native_index=77))),
                    replace(self.snapshot, objects=(first, replace(second, native_index=256))),
                    replace(self.snapshot, objects=(replace(first, class_id=2), second)),
                    replace(self.snapshot, objects=(replace(first, class_version=2), second)),
                    replace(self.snapshot, objects=(replace(first, fields={1: 90, 2: 3}), second)),
                    replace(self.snapshot, events=self.snapshot.events * 2),
                    replace(self.snapshot, events=(replace(self.snapshot.events[0], owner_id=3),)),
                    replace(self.snapshot, events=(replace(self.snapshot.events[0], event_id=0),)),
                    replace(self.snapshot, events=(replace(self.snapshot.events[0], event_type=3),)),
                    replace(self.snapshot, rngs={}),
                    replace(self.snapshot, objects=(replace(first, fields={1: 101, 2: 2}), second))]
        for snapshot in variants:
            with self.subTest(snapshot=snapshot), self.assertRaises(krs.SaveFormatError):
                krs.encode(snapshot, self.schema, self.compat)
        # A fully rechecksummed file cannot bypass graph validation.
        entries = chunks(self.data)
        objects = bytearray(entries[2][3])
        struct.pack_into("<I", objects, 4, 3)  # GAME still refers to ID 1.
        entries[2] = (*entries[2][:3], bytes(objects))
        self.rejects(rebuild(self.data, entries), "object_reference")

    def test_reference_class_whitelist_and_snapshot_invariants(self):
        first, second = self.snapshot.objects
        changed_schema = replace(self.schema, classes={**self.schema.classes,
                                                       (2, 1): self.schema.classes[1, 1]})
        invalid = replace(self.snapshot, objects=(first, replace(second, class_id=2)))
        with self.assertRaisesRegex(krs.SaveFormatError, "reference_type"):
            krs.encode(invalid, changed_schema, self.compat)
        self.rejects(rebuild(self.data, header_changes={16: struct.pack("<I", 2)}), "incompatible")
        with self.assertRaisesRegex(krs.SaveFormatError, "snapshot_value"):
            krs.decode(self.data, replace(self.schema, validate=lambda s: False), self.compat)

    def test_banks_use_fully_validated_generation_and_expose_damage(self):
        newer = krs.encode(replace(self.snapshot, generation=2), self.schema, self.compat)
        selected = krs.inspect_banks(newer, self.data, self.schema, self.compat)
        self.assertEqual((selected.current, selected.previous), ("A", "B"))
        self.assertEqual(krs.next_generation(selected), 3)
        damaged = newer[:-1] + bytes([newer[-1] ^ 1])
        selected = krs.inspect_banks(self.data, damaged, self.schema, self.compat)
        self.assertEqual((selected.current, selected.previous), ("A", None))
        self.assertIn("B", selected.rejected)
        self.assertEqual(krs.inspect_banks(None, None, self.schema, self.compat).current, None)
        self.assertEqual(krs.next_generation(krs.inspect_banks(None, None, self.schema, self.compat)), 1)
        wrong_language = krs.encode(self.snapshot, self.schema, replace(self.compat, language_id=2))
        selected = krs.inspect_banks(None, wrong_language, self.schema, self.compat)
        self.assertIsNone(selected.current)
        self.assertEqual(selected.rejected, {"B": "incompatible"})

    def test_equal_generations_and_overflow(self):
        selected = krs.inspect_banks(self.data, self.data, self.schema, self.compat)
        self.assertEqual((selected.current, selected.previous), ("A", None))
        different = krs.encode(replace(self.snapshot, play_ticks=999), self.schema, self.compat)
        with self.assertRaisesRegex(krs.SaveFormatError, "ambiguous_generation"):
            krs.inspect_banks(self.data, different, self.schema, self.compat)
        last = krs.encode(replace(self.snapshot, generation=krs.MAX_GENERATION),
                          self.schema, self.compat)
        with self.assertRaisesRegex(krs.SaveFormatError, "generation_overflow"):
            krs.next_generation(krs.inspect_banks(last, None, self.schema, self.compat))

    def test_manifest_is_sorted_and_has_explicit_lengths(self):
        a, b = (3, sha256(b"abc").digest()), (4, sha256(b"abcd").digest())
        manifest = {"level/objects": b, "level/map": a}
        expected = sha256()
        for key, (size, checksum) in sorted(manifest.items()):
            expected.update(struct.pack("<H", len(key)) + key.encode("ascii")
                            + struct.pack("<I", size) + checksum)
        self.assertEqual(krs.content_fingerprint(manifest), expected.digest())
        self.assertEqual(krs.content_fingerprint(dict(reversed(list(manifest.items())))), expected.digest())
        for bad_key in ("", "../map", "/map", "C:/map", "map//foo", "карта", "MAP"):
            with self.subTest(key=bad_key), self.assertRaises(krs.SaveFormatError):
                krs.content_fingerprint({bad_key: a})


if __name__ == "__main__":
    unittest.main()
