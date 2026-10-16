"""Calls survive hard interruptions as unfinished admissions, never zero usage."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import codex_evidence as evidence


class CallAdmissionJournalTests(unittest.TestCase):
    def test_admission_is_durable_before_invocation_and_interruption_is_explicit(self):
        quota = {'auth_type': 'chatgpt', 'ordinary_usage_allowed': True,
                 'buckets': {'codex': {'primary': {'usedPercent': 0}, 'credits': {'hasCredits': False}}}}
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / 'calls.jsonl'
            token = evidence.USAGE_JOURNAL.set(journal)
            self.addCleanup(evidence.USAGE_JOURNAL.reset, token)
            def interrupted(*args, **kwargs):
                admission = json.loads(journal.with_name('calls.admissions.jsonl').read_text())
                self.assertEqual(admission['state'], 'invocation-prepared')
                self.assertEqual(admission['product_ids'], ['1'])
                self.assertIsNone(admission['usage'])
                self.assertNotIn('prompt', admission)
                self.assertNotIn('env', admission)
                raise KeyboardInterrupt()
            with mock.patch.object(evidence, 'quota_snapshot', return_value=quota), mock.patch.object(evidence.subprocess, 'run', side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    evidence.run('isolated fixture', {'type': 'object'}, model='gpt-6-luna', effort='high',
                                 codex_bin='unused', stage='unit-interruption', product_ids=['1'],
                                 prompt_version='fixture', context_hash='fixture')
            completed = json.loads(journal.read_text())
            admission = json.loads(journal.with_name('calls.admissions.jsonl').read_text())
            self.assertEqual(completed['call_id'], admission['call_id'])
            self.assertEqual(completed['error'], 'KeyboardInterrupt')
            self.assertIsNone(completed['usage'])

    def test_stop_appearing_during_quota_check_prevents_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            stop = Path(directory) / 'STOP'
            old = evidence.STOP_FILE; evidence.STOP_FILE = stop
            self.addCleanup(setattr, evidence, 'STOP_FILE', old)
            def quota(*args):
                stop.write_text('Stop admissions')
                return {'auth_type': 'chatgpt', 'ordinary_usage_allowed': True,
                        'buckets': {'codex': {'primary': {'usedPercent': 0}}}}
            with mock.patch.object(evidence, 'quota_snapshot', side_effect=quota), mock.patch.object(evidence.subprocess, 'run') as invoked:
                with self.assertRaises(evidence.StopRequestedError):
                    evidence.run('fixture', {'type': 'object'}, model='gpt-6-luna', effort='high',
                                 codex_bin='unused', stage='unit-stop', product_ids=['1'],
                                 prompt_version='fixture', context_hash='fixture')
                invoked.assert_not_called()


if __name__ == '__main__':
    unittest.main()
