"""KRS v1 reference codec for development of the Kremlin save/load patch.

This is not a game-state adapter or a registered patch. No production schema is
defined: callers must supply a complete, independently verified schema. The DOS
payload must implement the same checks before replacing a live world.
"""
from dataclasses import dataclass
from hashlib import sha256
import struct
from typing import Callable, Mapping
from zlib import crc32


MAGIC = b"WASDKRS\0"
HEADER_SIZE = 128
MAX_FILE_SIZE = 1024 * 1024
MAX_OBJECTS = 4096
MAX_EVENTS = 16384
MAX_GENERATION = (1 << 64) - 1
TAGS = (b"GAME", b"MAPS", b"OBJS", b"EVNT", b"RNGS")
HEADER = struct.Struct("<8sHHIIIBBHQQ32s32sIII8s")
CHUNK = struct.Struct("<4sHHII")
FIELD = struct.Struct("<HBBI")
OBJECT = struct.Struct("<IHHHHI")
EVENT = struct.Struct("<IIHHI")
NUMBERS = {1: "<B", 2: "<H", 3: "<I", 4: "<h", 5: "<i", 6: "<Q", 7: "<I"}


class SaveFormatError(ValueError):
    """Stable development diagnostic, never a translated game message."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition, code):
    if not condition:
        raise SaveFormatError(code)


@dataclass(frozen=True)
class FieldSpec:
    wire_type: int
    # Every field has an explicit semantic validator, including byte arrays.
    validate: Callable[[int | bytes], bool]
    required: bool = True
    max_size: int = 8
    reference_classes: frozenset[int] | None = None


@dataclass(frozen=True)
class RecordSpec:
    fields: Mapping[int, FieldSpec]
    # Cross-field invariants (coordinates, indices, capacities, etc.).
    validate: Callable[[Mapping[int, int | bytes]], bool]


@dataclass(frozen=True)
class Schema:
    schema_id: int
    game: RecordSpec
    maps: RecordSpec
    rngs: RecordSpec
    classes: Mapping[tuple[int, int], RecordSpec]
    events: Mapping[tuple[int, int], RecordSpec]
    # Ownership, resource IDs, hero identity and ordered event relationships.
    validate: Callable[["Snapshot"], bool]


@dataclass(frozen=True)
class Compatibility:
    compat_id: int
    level_id: int
    language_id: int
    content_sha256: bytes


@dataclass(frozen=True)
class ObjectRecord:
    object_id: int
    class_id: int
    class_version: int
    native_index: int
    fields: Mapping[int, int | bytes]


@dataclass(frozen=True)
class EventRecord:
    event_id: int
    owner_id: int
    event_type: int
    event_version: int
    fields: Mapping[int, int | bytes]


@dataclass(frozen=True)
class Snapshot:
    generation: int
    play_ticks: int
    source_sha256: bytes
    game: Mapping[int, int | bytes]
    maps: Mapping[int, int | bytes]
    objects: tuple[ObjectRecord, ...]
    events: tuple[EventRecord, ...]
    rngs: Mapping[int, int | bytes]


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, size):
        _require(0 <= size <= len(self.data) - self.offset, "truncated")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def unpack(self, layout):
        return layout.unpack(self.take(layout.size))

    def done(self):
        _require(self.offset == len(self.data), "trailing_data")


def _pack(layout, *values):
    try:
        return layout.pack(*values)
    except (struct.error, TypeError, OverflowError) as exc:
        raise SaveFormatError("value_range") from exc


def _encode_fields(fields, spec):
    _require(set(fields) <= set(spec.fields), "unknown_field")
    _require(all(not f.required or key in fields for key, f in spec.fields.items()),
             "missing_field")
    result = bytearray()
    for key, value in sorted(fields.items()):
        field = spec.fields[key]
        _require(field.wire_type in (*NUMBERS, 8), "wire_type")
        if field.wire_type == 8:
            _require(type(value) is bytes, "field_value")
            encoded = value
        else:
            _require(type(value) is int, "field_value")
            encoded = _pack(struct.Struct(NUMBERS[field.wire_type]), value)
        _require(len(encoded) <= field.max_size, "field_size")
        _require(field.validate(value), "field_value")
        result += _pack(FIELD, key, field.wire_type, 0, len(encoded)) + encoded
        _require(len(result) <= MAX_FILE_SIZE, "file_size")
    _require(spec.validate(fields), "record_value")
    return bytes(result)


def _decode_fields(data, spec):
    reader, result, previous = _Reader(data), {}, -1
    while reader.offset < len(data):
        key, wire, reserved, size = reader.unpack(FIELD)
        _require(key > previous, "field_order")
        previous = key
        _require(reserved == 0, "reserved")
        _require(key in spec.fields, "unknown_field")
        field = spec.fields[key]
        _require(wire == field.wire_type and wire in (*NUMBERS, 8), "wire_type")
        _require(size <= field.max_size, "field_size")
        if wire == 8:
            value = reader.take(size)
        else:
            layout = struct.Struct(NUMBERS[wire])
            _require(size == layout.size, "field_size")
            value, = reader.unpack(layout)
        _require(field.validate(value), "field_value")
        result[key] = value
    _require(all(not f.required or key in result for key, f in spec.fields.items()),
             "missing_field")
    _require(spec.validate(result), "record_value")
    return result


def _record_spec(registry, key):
    _require(key in registry, "unknown_record_type")
    return registry[key]


def _validate_graph(snapshot, schema):
    objects, indices, events = {}, set(), set()
    _require(len(snapshot.objects) <= MAX_OBJECTS, "object_limit")
    _require(len(snapshot.events) <= MAX_EVENTS, "event_limit")
    for obj in snapshot.objects:
        _require(1 <= obj.object_id <= 0xffffffff and obj.object_id not in objects,
                 "object_id")
        _require(obj.native_index == 0xffff or 0 <= obj.native_index <= 255, "native_index")
        if obj.native_index != 0xffff:
            _require(obj.native_index not in indices, "native_index")
            indices.add(obj.native_index)
        objects[obj.object_id] = obj

    def references(fields, spec):
        for key, value in fields.items():
            field = spec.fields[key]
            if field.wire_type != 7 or value == 0:
                continue
            _require(value in objects, "object_reference")
            _require(field.reference_classes is None
                     or objects[value].class_id in field.reference_classes, "reference_type")

    for fields, spec in ((snapshot.game, schema.game), (snapshot.maps, schema.maps),
                         (snapshot.rngs, schema.rngs)):
        references(fields, spec)
    for obj in snapshot.objects:
        references(obj.fields, _record_spec(schema.classes, (obj.class_id, obj.class_version)))
    for event in snapshot.events:
        _require(1 <= event.event_id <= 0xffffffff and event.event_id not in events, "event_id")
        events.add(event.event_id)
        _require(event.owner_id in objects, "event_owner")
        references(event.fields, _record_spec(schema.events, (event.event_type, event.event_version)))
    # Object-reference cycles are permitted. Ring and ownership semantics belong
    # to the complete schema, not to a generic acyclic-graph check.
    _require(schema.validate(snapshot), "snapshot_value")


def _validate_compatibility(schema, compatibility):
    _require(1 <= schema.schema_id <= 0xffffffff, "schema_id")
    _require(1 <= compatibility.compat_id <= 0xffffffff, "compat_id")
    _require(compatibility.level_id in (1, 2, 3), "level_id")
    _require(compatibility.language_id in (1, 2), "language_id")
    _require(len(compatibility.content_sha256) == 32, "content_hash")


def encode(snapshot: Snapshot, schema: Schema, compatibility: Compatibility) -> bytes:
    """Encode a fully schema-validated logical snapshot, never a memory dump."""
    _validate_compatibility(schema, compatibility)
    _require(1 <= snapshot.generation <= MAX_GENERATION, "generation")
    _require(0 <= snapshot.play_ticks <= MAX_GENERATION, "play_ticks")
    _require(len(snapshot.source_sha256) == 32, "source_hash")
    _require(len(snapshot.objects) <= MAX_OBJECTS, "object_limit")
    _require(len(snapshot.events) <= MAX_EVENTS, "event_limit")
    payloads = [_encode_fields(snapshot.game, schema.game),
                _encode_fields(snapshot.maps, schema.maps)]
    objects = bytearray(struct.pack("<I", len(snapshot.objects)))
    for obj in snapshot.objects:
        spec = _record_spec(schema.classes, (obj.class_id, obj.class_version))
        fields = _encode_fields(obj.fields, spec)
        objects += _pack(OBJECT, obj.object_id, obj.class_id, obj.class_version,
                         obj.native_index, 0, len(fields)) + fields
        _require(len(objects) <= MAX_FILE_SIZE, "file_size")
    payloads.append(bytes(objects))
    events = bytearray(struct.pack("<I", len(snapshot.events)))
    for event in snapshot.events:
        spec = _record_spec(schema.events, (event.event_type, event.event_version))
        fields = _encode_fields(event.fields, spec)
        events += _pack(EVENT, event.event_id, event.owner_id, event.event_type,
                        event.event_version, len(fields)) + fields
        _require(len(events) <= MAX_FILE_SIZE, "file_size")
    payloads += [bytes(events), _encode_fields(snapshot.rngs, schema.rngs)]
    _validate_graph(snapshot, schema)
    body = b"".join(CHUNK.pack(tag, 1, 1, len(payload), crc32(payload)) + payload
                    for tag, payload in zip(TAGS, payloads))
    size = HEADER_SIZE + len(body)
    _require(size <= MAX_FILE_SIZE, "file_size")
    header = HEADER.pack(MAGIC, 1, HEADER_SIZE, size, schema.schema_id,
                         compatibility.compat_id, compatibility.level_id,
                         compatibility.language_id, 0, snapshot.generation,
                         snapshot.play_ticks, compatibility.content_sha256,
                         snapshot.source_sha256, 5, crc32(body), 0, bytes(8))
    return header[:116] + struct.pack("<I", crc32(header)) + header[120:] + body


def decode(data: bytes, schema: Schema, compatibility: Compatibility) -> Snapshot:
    """Check the entire file and schema before exposing any restorable state."""
    _validate_compatibility(schema, compatibility)
    _require(HEADER_SIZE <= len(data) <= MAX_FILE_SIZE, "file_size")
    reader = _Reader(data)
    (magic, version, header_size, file_size, schema_id, compat_id, level, language,
     flags, generation, play_ticks, content_hash, source_hash, chunk_count,
     body_crc, header_crc, reserved) = reader.unpack(HEADER)
    _require(magic == MAGIC, "magic")
    _require(version == 1 and header_size == HEADER_SIZE, "container_version")
    _require(file_size == len(data), "file_size")
    _require(flags == 0 and reserved == bytes(8), "reserved")
    _require(1 <= chunk_count <= 32, "chunk_count")
    _require(generation != 0, "generation")
    _require(crc32(data[:116] + bytes(4) + data[120:128]) == header_crc, "header_crc")
    _require(crc32(data[128:]) == body_crc, "body_crc")
    _require((schema_id, compat_id, level, language, content_hash) ==
             (schema.schema_id, compatibility.compat_id, compatibility.level_id,
              compatibility.language_id, compatibility.content_sha256), "incompatible")
    payloads = {}
    for _ in range(chunk_count):
        tag, version, flags, size, checksum = reader.unpack(CHUNK)
        _require(all(0x20 <= ch <= 0x7e for ch in tag), "chunk_tag")
        _require(flags & ~1 == 0, "chunk_flags")
        payload = reader.take(size)
        _require(crc32(payload) == checksum, "chunk_crc")
        if tag not in TAGS:
            _require(not flags & 1, "unknown_required_chunk")
            continue
        _require(tag not in payloads, "duplicate_chunk")
        _require(tag == TAGS[len(payloads)], "chunk_order")
        _require(version == 1 and flags == 1, "chunk_version")
        payloads[tag] = payload
    reader.done()
    _require(tuple(payloads) == TAGS, "missing_chunk")
    objects_reader = _Reader(payloads[b"OBJS"])
    count, = objects_reader.unpack(struct.Struct("<I"))
    _require(count <= MAX_OBJECTS, "object_limit")
    objects = []
    for _ in range(count):
        object_id, cls, version, index, reserved, size = objects_reader.unpack(OBJECT)
        _require(reserved == 0, "reserved")
        spec = _record_spec(schema.classes, (cls, version))
        fields = _decode_fields(objects_reader.take(size), spec)
        objects.append(ObjectRecord(object_id, cls, version, index, fields))
    objects_reader.done()
    events_reader = _Reader(payloads[b"EVNT"])
    count, = events_reader.unpack(struct.Struct("<I"))
    _require(count <= MAX_EVENTS, "event_limit")
    events = []
    for _ in range(count):
        event_id, owner, kind, version, size = events_reader.unpack(EVENT)
        spec = _record_spec(schema.events, (kind, version))
        fields = _decode_fields(events_reader.take(size), spec)
        events.append(EventRecord(event_id, owner, kind, version, fields))
    events_reader.done()
    snapshot = Snapshot(generation, play_ticks, source_hash,
                        _decode_fields(payloads[b"GAME"], schema.game),
                        _decode_fields(payloads[b"MAPS"], schema.maps),
                        tuple(objects), tuple(events),
                        _decode_fields(payloads[b"RNGS"], schema.rngs))
    _validate_graph(snapshot, schema)
    return snapshot


def content_fingerprint(resources: Mapping[str, tuple[int, bytes]]) -> bytes:
    """Hash an adapter's complete manifest; keys are normalized internal IDs."""
    _require(bool(resources), "empty_manifest")
    digest = sha256()
    for key in sorted(resources):
        _require(bool(key) and all(ch in "abcdefghijklmnopqrstuvwxyz0123456789-_./"
                                  for ch in key), "resource_id")
        _require(not key.startswith("/") and all(p not in ("", ".", "..")
                                                for p in key.split("/")), "resource_id")
        encoded = key.encode("ascii")
        size, checksum = resources[key]
        _require(type(checksum) is bytes and len(checksum) == 32, "resource_hash")
        digest.update(_pack(struct.Struct("<H"), len(encoded)))
        digest.update(encoded)
        digest.update(_pack(struct.Struct("<I"), size))
        digest.update(checksum)
    return digest.digest()


@dataclass(frozen=True)
class BankSelection:
    # Caller must present damaged/incompatible banks to the user. This function
    # never loads a world, promotes TMP, or silently falls back after corruption.
    current: str | None
    previous: str | None
    snapshots: Mapping[str, Snapshot]
    rejected: Mapping[str, str]


def inspect_banks(a: bytes | None, b: bytes | None, schema: Schema,
                  compatibility: Compatibility) -> BankSelection:
    _validate_compatibility(schema, compatibility)
    valid, rejected, raw = {}, {}, {"A": a, "B": b}
    for name, data in raw.items():
        if data is None:
            continue
        try:
            valid[name] = decode(data, schema, compatibility)
        except SaveFormatError as exc:
            rejected[name] = exc.code
    order = sorted(valid, key=lambda name: -valid[name].generation)
    if len(order) == 2 and valid["A"].generation == valid["B"].generation:
        _require(a == b, "ambiguous_generation")
        # Identical files represent one logical successful write, not a prior one.
        order = ["A"]
    return BankSelection(order[0] if order else None,
                         order[1] if len(order) > 1 else None, valid, rejected)


def next_generation(selection: BankSelection) -> int:
    generation = max((s.generation for s in selection.snapshots.values()), default=0)
    _require(generation < MAX_GENERATION, "generation_overflow")
    return generation + 1
