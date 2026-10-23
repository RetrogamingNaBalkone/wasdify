"""Private prototype file transactions use only invented snapshots."""
import copy
from contextlib import redirect_stdout, redirect_stderr
import io
import struct
import json
from importlib.resources import files
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from tests.support import temporary_directory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    import play_kremlin_save as prototype
finally:
    sys.path.pop(0)


def packet():
    return {"revision": prototype.REVISION, "resources": [], "baseline_inventory": [],
            "state": {"maps": [bytes(4096)] * 4}, "events": [], "doors": [], "next_attempt": 2}


class PrototypeFileTests(unittest.TestCase):
    def test_attempt_counter_is_required(self):
        current = {**packet(), 'next_attempt': 4}
        self.assertEqual(prototype.decode(prototype.encode(current)), current)
        incomplete = packet()
        del incomplete['next_attempt']
        with self.assertRaisesRegex(ValueError, 'snapshot_sections'):
            prototype.encode(incomplete)
        for invalid in (0, -1, True, '2', 32768):
            with self.assertRaisesRegex(ValueError, 'snapshot_attempt'):
                prototype.encode({**current, 'next_attempt': invalid})

    def test_game_message_keys_and_choice_labels_match_both_languages(self):
        messages = json.loads(files('wasdify').joinpath('data/kremlin_save_prototype_messages.json').read_text('utf-8'))
        self.assertEqual(set(messages['ru']), set(messages['en']))
        for language, values in messages.items():
            for value in values.values():
                self.assertEqual(value, value.upper())
            session = prototype.Session.__new__(prototype.Session)
            session.game_messages = values
            for selected in (0, 1):
                text = session.choice_text(selected)
                self.assertLessEqual(len(text.encode('cp866')), 255)
                self.assertEqual(text.count('> '), 1)
            for key in ('choice_title', 'choice_start', 'choice_load'):
                self.assertEqual(values[key], prototype.Translator(language)('prototype_' + key))

    def test_death_choice_winning_a_save_race_keeps_the_gate_held(self):
        session = prototype.Session.__new__(prototype.Session)
        session.live, session.transport, session.api = True, 0, Mock()
        session.message = Mock()
        with patch.object(prototype, 'pause_at_boundary', return_value=b'\x02\0') as pause:
            with self.assertRaises(prototype.DeathChoicePending):
                session.progress('saving')
        pause.assert_called_once_with(session.api, 0, allow_death=True)
        session.message.assert_not_called()
        session.api.put.assert_not_called()

    def test_no_save_bypasses_startup_choice_and_releases_native_death(self):
        session = prototype.Session.__new__(prototype.Session)
        session.live = True
        session.refresh_available = Mock(return_value=False)
        session.release_gate = Mock()
        session.restart_level = Mock()
        session.load = Mock()
        self.assertIsNone(session.choose_level_action())
        session.release_gate.assert_not_called()
        self.assertIsNone(session.choose_level_action(death=True))
        session.release_gate.assert_called_once()
        session.restart_level.assert_not_called()
        session.load.assert_not_called()

    def test_initial_binding_keeps_the_first_frame_until_startup_choice(self):
        session = prototype.Session.__new__(prototype.Session)
        session.api, session.transport = Mock(), 123
        with patch.object(prototype, 'observe') as observe:
            session.bind(startup=True, manual_start=True)
        self.assertTrue(observe.call_args.kwargs['keep_gate'])
        self.assertTrue(observe.call_args.kwargs['manual_start'])
        self.assertTrue(session.startup_gate)
        session.live = True
        session.refresh_available = Mock(return_value=False)
        session.release_gate = Mock()
        self.assertIsNone(session.choose_level_action())
        session.release_gate.assert_called_once()
        self.assertFalse(session.startup_gate)

    def test_manual_start_never_injects_keys_while_waiting_for_user(self):
        api = Mock()
        api.get.side_effect = [bytes(2), bytes(2), OSError('user closed game')]
        with patch.object(prototype.time, 'sleep'):
            with self.assertRaisesRegex(OSError, 'user closed game'):
                prototype.observe(api, 100, 'ru', 1, dismiss_intro=False,
                                  runtime_layout=(0, {}), manual_start=True)
        api.put.assert_not_called()

    def test_intro_enter_release_does_not_confirm_new_choice(self):
        session = prototype.Session.__new__(prototype.Session)
        session.live, session.startup_gate = True, True
        session.api, session.ds, session.transport = Mock(), 0, 0
        session.world, session.ldt = 0, {}
        session.reports, session.snapshot_path = [], Path('INVENTED.PROTO')
        session.refresh_available = Mock(return_value=True)
        session.clear_keyboard, session.draw_dialog = Mock(), Mock()
        session.choice_text = Mock(return_value='CHOICE')
        session.load = Mock()
        states = iter((b'\x02', b'\x03', b'\x02'))
        reads = []

        def get(path):
            if path == f'memory/{0x2155 + 0x1c}/1':
                value = next(states)
                reads.append(value)
                if len(reads) < 3:
                    session.load.assert_not_called()
                return value
            return b'\0'

        session.api.get.side_effect = get
        view = Mock()
        view.pointer.return_value = 0
        view.u16.return_value = 100
        with patch.object(prototype, 'Memory', return_value=view), patch.object(prototype.time, 'sleep'):
            self.assertEqual(session.choose_level_action(), 'load')
        session.load.assert_called_once_with(session.snapshot_path)
        self.assertEqual(reads, [b'\x02', b'\x03', b'\x02'])

    def test_world_reset_failure_cannot_be_treated_as_a_recoverable_file_error(self):
        with temporary_directory() as directory:
            path = Path(directory) / 'TEST.PROTO'
            path.write_bytes(prototype.encode(packet()))
            session = prototype.Session.__new__(prototype.Session)
            session.live = True
            session.manifest, session.baseline_inventory = [], []
            session.api = Mock()
            session.api.get.return_value = b'\x01\0'
            session.ldt, session.world, session.transport = 0, 0, 0
            session.queue = object()
            session.draw_dialog = Mock()
            session.game_messages = {'loading': 'Loading game...'}
            session.current_world = Mock()
            session.validate_packet = Mock()
            session.reset_world = Mock(side_effect=ValueError('invented reconstruction failure'))
            with patch.object(prototype, 'Memory'), patch.object(prototype, 'frame_objects', return_value={}):
                with self.assertRaisesRegex(RuntimeError, 'prototype_world_reset_interrupted'):
                    session.load(path)
            session.validate_packet.assert_called_once()
            session.reset_world.assert_called_once()

    def test_native_command_publishes_arguments_atomically_and_handles_counter_wrap(self):
        for counter in (7, 0xffff):
            with self.subTest(counter=counter):
                data = bytearray(256)
                struct.pack_into('<H', data, 44, 1)
                struct.pack_into('<H', data, 118, counter)
                calls = []

                class Api:
                    def get(self, path):
                        calls.append(('get', path))
                        _, address, size = path.split('/')
                        return bytes(data[int(address):int(address) + int(size)])

                    def put(self, address, raw, expected=None):
                        calls.append(('put', address))
                        assert address == '116' and bytes(data[116:132]) == expected
                        assert struct.unpack_from('<H', raw)[0] == 1
                        assert struct.unpack_from('<HH', raw, 12) == (2, 5)
                        assert struct.unpack_from('<H', raw, 2)[0] == counter
                        data[116:132] = raw
                        struct.pack_into('<3H', data, 116, 0, (counter + 1) & 0xffff, 0)
                        data[136:140] = b'ptr!'

                self.assertEqual(prototype.native_call(Api(), 0, 1, 2, 5), (0, b'ptr!'))
                self.assertEqual(len(calls), 3)

    def test_parallel_restore_prevalidates_all_writes_and_preserves_gaps(self):
        data = bytearray(b"a" * 64)
        calls = []

        class Api:
            def get(self, path):
                calls.append(path)
                return bytes(data)

            def put(self, address, raw, expected=None):
                address = int(address)
                self_guard = bytes(data[address:address + len(expected)])
                if self_guard != expected:
                    raise OSError('conditional write failed')
                data[address:address + len(raw)] = raw

        write = prototype.event_adapter.NativeWrite
        plan = (write(8, b"aa", b"XY"), write(20, b"aa", b"ZZ"))
        data[20] = ord('!')
        before = bytes(data)
        with self.assertRaisesRegex(ValueError, 'changed before restoration'):
            prototype.apply_component_plan(Api(), plan, workers=2)
        self.assertEqual(bytes(data), before)
        data[20] = ord('a')
        calls.clear()
        prototype.apply_component_plan(Api(), plan, workers=2)
        self.assertEqual(calls, ['memory/0/0x2000000'])
        self.assertEqual(bytes(data), b'a' * 8 + b'XY' + b'a' * 10 + b'ZZ' + b'a' * 42)

    def test_parallel_failure_waits_for_outstanding_writes_before_returning(self):
        started = threading.Event()
        completed = threading.Event()

        class Api:
            def get(self, path):
                return bytes(64)

            def put(self, address, raw, expected=None):
                if int(address) == 8:
                    if not started.wait(2):
                        raise AssertionError('independent writes did not run concurrently')
                    raise OSError('invented failure')
                started.set()
                completed.set()

        write = prototype.event_adapter.NativeWrite
        with self.assertRaisesRegex(OSError, 'invented failure'):
            prototype.apply_component_plan(Api(), (write(8, b'\0', b'A'), write(20, b'\0', b'B')), workers=2)
        self.assertTrue(completed.is_set())

    def test_keyboard_irq_between_plan_and_commit_does_not_abort_state_installation(self):
        class Memory:
            def __init__(self):
                self.data = bytearray(0x4000)

            def pointer(self, address, size):
                return 0x100

            def read(self, address, size):
                return bytes(self.data[address:address + size])

        memory = Memory()
        session = prototype.Session.__new__(prototype.Session)
        session.ds = 0
        plan = session.input_plan(memory)
        # A physical F9 release arrives after the memory snapshot was taken.
        memory.data[0x2152] = 1
        memory.data[0x2155 + 0x43] = 2
        memory.data[0x21d5] = 0xa5

        class Api:
            def get(self, path):
                _, address, size = path.split('/')
                return memory.read(int(address), int(size))

            def put(self, address, raw, expected=None):
                address = int(address)
                if expected is not None and memory.read(address, len(raw)) != expected:
                    raise OSError('HTTP 412: keyboard IRQ changed the expected bytes')
                memory.data[address:address + len(raw)] = raw

        session.api = Api()
        prototype.apply_component_plan(session.api, plan)
        session.clear_keyboard()
        self.assertEqual(memory.read(0x2152, 131), bytes(131))
        self.assertEqual(memory.data[0x21d5], 0xa5)

    def test_console_and_fatal_traceback_survive_in_session_log(self):
        with temporary_directory() as directory:
            log = Path(directory) / 'session.log'
            console = io.StringIO()
            with redirect_stdout(console), redirect_stderr(console):
                with self.assertRaisesRegex(RuntimeError, 'invented load failure'):
                    with prototype.session_log(log):
                        print('operation started')
                        raise RuntimeError('invented load failure')
            recorded = log.read_text('utf-8')
            self.assertIn('operation started', recorded)
            self.assertIn('Traceback', recorded)
            self.assertIn('invented load failure', recorded)

    def test_round_trip_corruption_and_malformed_structure(self):
        value = packet()
        raw = prototype.encode(value)
        self.assertEqual(prototype.decode(raw), value)
        for damaged in (raw[:-1], b'null', b'[]', b'{"payload":null}',
                        b'{"payload":null,"payload":null,"sha256":"x"}',
                        raw.replace(b'"resources":[]', b'"resources":[1]')):
            with self.subTest(damaged=damaged[:30]), self.assertRaises(ValueError):
                prototype.decode(damaged)

    def test_previous_snapshot_and_owned_temporary_cleanup(self):
        with temporary_directory() as directory:
            target = Path(directory) / "KRS1.PROTO"
            first = packet()
            prototype.store_snapshot(target, first)
            old = target.read_bytes()
            second = copy.deepcopy(first)
            second["resources"] = ["changed"]
            prototype.store_snapshot(target, second)
            self.assertEqual(prototype.decode(target.read_bytes()), second)
            self.assertEqual(target.with_suffix(".PRV").read_bytes(), old)
            self.assertFalse(target.with_suffix(".TMP").exists())
            self.assertFalse(target.with_suffix(".BKT").exists())

    def test_failed_backup_or_commit_preserves_last_snapshot(self):
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at), temporary_directory() as directory:
                target = Path(directory) / "KRS1.PROTO"
                prototype.store_snapshot(target, packet())
                old = target.read_bytes()
                replacement = packet()
                replacement["resources"] = ["new"]
                real_replace = prototype.os.replace
                count = 0

                def replace(source, destination):
                    nonlocal count
                    count += 1
                    if count == fail_at:
                        raise OSError("invented disk failure")
                    return real_replace(source, destination)

                with patch.object(prototype.os, "replace", side_effect=replace), self.assertRaises(OSError):
                    prototype.store_snapshot(target, replacement)
                self.assertEqual(target.read_bytes(), old)
                self.assertFalse(target.with_suffix(".TMP").exists())
                self.assertFalse(target.with_suffix(".BKT").exists())

    def test_foreign_temporary_is_preserved(self):
        with temporary_directory() as directory:
            target = Path(directory) / "KRS1.PROTO"
            temporary = target.with_suffix(".TMP")
            temporary.write_bytes(b"unrelated existing file")
            with self.assertRaises(FileExistsError):
                prototype.store_snapshot(target, packet())
            self.assertFalse(target.exists())
            self.assertEqual(temporary.read_bytes(), b"unrelated existing file")
