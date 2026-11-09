"""CPU regression checks for explicit private configuration and fail-fast lookup."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import physevo_release as release


class PrivateConfigTests(unittest.TestCase):
    def test_location_keeps_content_identity(self):
        record = {'sha256': 'a' * 64, 'bytes': 123}
        settings = {'locations': {'a' * 64: 'hdfs://storage.example.org/assets.tar.gz'}}
        resolved = release.located(record, settings)
        self.assertEqual(resolved, {**record, 'uri': settings['locations']['a' * 64]})
        self.assertNotIn('uri', record)

    def test_absent_and_placeholder_locations_are_rejected(self):
        record = {'sha256': 'a' * 64}
        with self.assertRaises(KeyError):
            release.located(record, {'locations': {}})
        for uri in ('hdfs://storage.example.invalid/placeholder', 'file:///tmp/archive',
                    'hdfs://storage.example.org/', 'hdfs://user:password@storage.example.org/data'):
            with self.assertRaises(ValueError):
                release.located(record, {'locations': {'a' * 64: uri}})

    def test_missing_location_precedes_worker_or_network_calls(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'private.json'
            release.save(path, {'schema_version': 1, 'locations': {}})
            for action in (release.restore_assets, release.restore_runtime):
                with patch.object(release, 'verify'), patch.object(release, 'admit') as admit, \
                     patch.object(release.subprocess, 'run') as run:
                    with self.assertRaises(KeyError):
                        action(SimpleNamespace(private_config=path))
                    admit.assert_not_called()
                    run.assert_not_called()

    def test_example_covers_all_required_resources_without_real_locations(self):
        example = release.read(release.RELEASE / 'private-config.example.json')
        records = [release.read(release.RELEASE / 'dependencies.json')['download_probe'],
                   release.read(release.RELEASE / 'manifests/worker-runtime.json'),
                   *release.read(release.RELEASE / 'manifests/random12-assets.json')['parts']]
        self.assertEqual(set(example['locations']), {r['sha256'] for r in records})
        for record in records:
            self.assertNotIn('uri', record)
            with self.assertRaises(ValueError):
                release.located(record, example)

    def test_prepare_rejects_example_before_source_access(self):
        args = SimpleNamespace(private_config=release.RELEASE / 'private-config.example.json')
        with patch.object(release, 'verify'), patch.object(release, 'source_check') as source:
            with self.assertRaises(ValueError):
                release.prepare(args)
            source.assert_not_called()

    def test_run_rejects_missing_output_location_before_worker_access(self):
        for value in ('', '/runs', 'hdfs://storage.example.invalid/runs', 'hdfs://storage.example.org/'):
            with patch.object(release, 'verify'), patch.object(release, 'admit') as admit:
                with self.assertRaises(ValueError):
                    release.run(SimpleNamespace(hdfs_root=value))
                admit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
