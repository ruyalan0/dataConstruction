"""Merged experiments admit exact skill updates and reject unrelated source drift."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import verify_originals as sources


class MergedSourceTests(unittest.TestCase):
    def test_exact_skill_updates_and_parent_copy_verify(self):
        self.assertTrue(sources.verify()['passed'])
        expected = json.loads((sources.ROOT / 'release/dependencies.json').read_text())
        self.assertEqual(sources.verify_release(), expected)

    def test_unrelated_parent_file_drift_is_rejected(self):
        hashes = json.loads((HERE / 'parent-speed-sha256.json').read_text())
        name = next(name for name in hashes if name.endswith('/run_local.py'))
        altered = dict(hashes, **{name: '0' * 64})
        original = sources.updated_pins
        def changed_pins(values):
            values = values.copy()
            values[name] = altered[name]
            return original(values)
        with patch.object(sources, 'updated_pins', side_effect=changed_pins):
            self.assertIn(name, sources.verify()['differences'])
            with self.assertRaisesRegex(ValueError, 'Protected source'):
                sources.verify_release()

    def test_unrelated_release_file_drift_is_rejected(self):
        actual = sources.inventory(sources.ROOT)
        name = 'release/harness/egg_harness/action_control.py'
        altered = dict(actual, **{name: '0' * 64})
        with patch.object(sources, 'inventory', return_value=altered):
            with self.assertRaisesRegex(ValueError, 'Release inventory'):
                sources.verify_release()

    def test_skill_update_must_match_the_original_pin(self):
        updates = sources.source_updates()
        name = next(iter(updates))
        updates[name]['previous_sha256'] = '0' * 64
        with patch.object(sources, 'source_updates', return_value=updates):
            with self.assertRaisesRegex(ValueError, 'original pin'):
                sources.verify()


if __name__ == '__main__':
    unittest.main()
