"""Snapshot copying keeps concurrency guarantees without rereading all history."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import refresh_whole_product as refresh


class BoundCandidateCloneTests(unittest.TestCase):
    def prepare(self, root):
        source = root / 'source.sqlite'
        with closing(sqlite3.connect(source)) as conn, conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT)')
            conn.execute("INSERT INTO records VALUES (1,'Accepted')")
        with closing(refresh.readonly(source)) as conn:
            expected = refresh.fingerprint(conn)
        return source, root / 'copy.sqlite', expected

    def test_copy_blocks_other_writers_preserves_rows_and_reads_history_once(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, expected = self.prepare(Path(directory))
            original_clone = refresh.clone
            def copy_with_concurrent_attempt(source, target):
                with closing(sqlite3.connect(source, timeout=0)) as competing:
                    with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                        competing.execute("UPDATE records SET value='Concurrent'")
                original_clone(source, target)
            with mock.patch.object(refresh, 'clone', side_effect=copy_with_concurrent_attempt), mock.patch.object(refresh, 'fingerprint', wraps=refresh.fingerprint) as fingerprints:
                result = refresh.clone_bound_candidate(source, target, expected)
                self.assertEqual(fingerprints.call_count, 1)
            self.assertEqual(result['predecessor_decision_writes'], 0)
            for path in (source, target):
                with closing(refresh.readonly(path)) as conn:
                    self.assertEqual(refresh.fingerprint(conn), expected)

    def test_changed_baseline_and_wrong_interrupted_copy_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, expected = self.prepare(Path(directory))
            with self.assertRaisesRegex(refresh.GateError, 'changed after validation'):
                refresh.clone_bound_candidate(source, target, 'wrong')
            refresh.clone(source, target)
            with closing(sqlite3.connect(target)) as conn, conn:
                conn.execute("UPDATE records SET value='Wrong clone'")
            with self.assertRaisesRegex(refresh.GateError, 'Interrupted fork clone'):
                refresh.clone_bound_candidate(source, target, expected)

    def test_exact_byte_check_rejects_changes_outside_sqlite_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target, expected = self.prepare(Path(directory))
            original_clone = refresh.clone
            def changed_file(source, target):
                original_clone(source, target)
                with source.open('ab') as stream:
                    stream.write(b'Unexpected concurrent file change')
            with mock.patch.object(refresh, 'clone', side_effect=changed_file):
                with self.assertRaisesRegex(refresh.GateError, 'Concurrent previous-candidate'):
                    refresh.clone_bound_candidate(source, target, expected)
