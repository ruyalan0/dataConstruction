"""Exercise real offline release restoration without a GPU, API key, or NAS history."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from contextlib import nullcontext
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import physevo_release as release
from release_resources import admit
from seal_release import inventory, SCRIPTS


def check_infrastructure_summary(work, pins):
    campaign = work / 'infra-failure'
    campaign.mkdir()
    for name in ('runtime', 'credentials', 'memory'):
        (work / name).touch()
    config = {'robot_runtime': str(work / 'runtime'), 'zzy_credentials': str(work / 'credentials'),
              'memory': str(work / 'memory'), 'assets': str(work / 'Assets'), 'robodojo_root': str(work)}
    path = campaign / 'configs/fixture.json'
    release.save(path, config)
    release.save(campaign / 'plan.json', {'task': 'fixture', 'run_id': 'fixture',
        'config_sha256': release.sha(path), 'release_manifest_sha256': release.sha(release.RELEASE / 'manifest.json'),
        'episode_timeout_seconds': 60})
    runner = types.ModuleType('run_eight_eval')
    def failure(*args, **kwargs):
        raise RuntimeError('Fixture: HDFS readback failed')
    runner.run_case = failure
    runner.BACKUPS = SimpleNamespace(shutdown=lambda **kwargs: None)
    concurrency = types.ModuleType('provider_concurrency')
    concurrency.zzy_request_slot = lambda **kwargs: nullcontext()
    reporting = types.ModuleType('report_eight_eval')
    reporting.end_reason = lambda *args: 'execution_error'
    original_read = release.read
    def read(path):
        return {'parts': []} if Path(path).name == 'random12-assets.json' else original_read(path)
    report = {'worker_id': 'fixture', 'gpus': [{'index': '0', 'total_mib': '48000', 'used_mib': '0'}]}
    args = SimpleNamespace(campaign=campaign, gpu=0, scratch=work, local_resources=work / 'local.json',
                           lock_dir=work / 'locks', hdfs_root='hdfs://fixture/runs')
    with patch.object(release, 'verify', return_value=pins), patch.object(release, 'admit', return_value=report), \
         patch.object(release, 'source_check'), patch.object(release, 'read', side_effect=read), \
         patch.object(release.subprocess, 'run'), patch.dict(os.environ, {}, clear=True), \
         patch.object(sys, 'path', list(sys.path)), \
         patch.dict(sys.modules, {'run_eight_eval': runner, 'provider_concurrency': concurrency, 'report_eight_eval': reporting}):
        try:
            release.run(args)
        except RuntimeError as error:
            assert str(error) == 'Fixture: HDFS readback failed'
        else:
            raise AssertionError('Infrastructure exception was swallowed')
    summary = release.read(campaign / 'summary.json')
    assert summary['score'] is None and summary['valid_measurement'] is False


def main():
    pins = release.verify()
    with tempfile.TemporaryDirectory(prefix='physevo-offline-') as temporary:
        work = Path(temporary)
        # Exact inventory must reject added executable files as well as modified ones.
        tiny = work / 'inventory'
        (tiny / 'release/evaluation/scripts').mkdir(parents=True)
        (tiny / 'scripts').mkdir()
        for name in SCRIPTS:
            (tiny / 'scripts' / name).touch()
        release.save(tiny / 'release/manifest.json', {'files': inventory(tiny)})
        (tiny / 'release/evaluation/scripts/openai.py').write_text('raise RuntimeError("unexpected module")\n')
        with patch.object(release, 'ROOT', tiny), patch.object(release, 'RELEASE', tiny / 'release'):
            try:
                release.verify()
            except ValueError as error:
                assert 'inventory' in str(error)
            else:
                raise AssertionError('Unlisted executable file was accepted')
        attempted = []
        def fail_first(item):
            attempted.append(item[0])
            raise RuntimeError('Fixture shard checksum failure')
        try:
            release.restore_parts(list(range(20)), 1, fail_first)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Shard failure was swallowed')
        assert attempted == [0], attempted
        check_infrastructure_summary(work, pins)
        execution_error = {'status': 'execution_error', 'native': {'native_terminal': True,
                           'success': True, 'score': 100, 'valid_sample': True}}
        assert not release.valid_measurement(execution_error, 'native_success')
        assert release.valid_measurement(dict(execution_error, status='policy_completed'), 'native_success')
        source = work / 'RoboDojo'
        release.restore_sources(SimpleNamespace(destination=source))
        for repo in (source, source / 'XPolicyLab'):
            assert not (repo / '.git/objects/info/alternates').exists()
        tasks = sorted(p.stem for p in (release.RELEASE / 'templates/random12').glob('*_random.json'))
        assert len(tasks) == 12
        private = work / 'private.json'
        release.save(private, {'schema_version': 1, 'provider_base_urls': {
            'zzy-astra': 'https://gateway.example.org/v1'}})
        for task in tasks:
            output = work / task
            release.prepare(SimpleNamespace(task=task, layout=20, robodojo=source,
                assets=work / 'assets/Assets', sim_python=work / 'sim/bin/python',
                credentials=work / 'private-key-file', private_config=private, output=output))
            config = release.read(output / 'configs' / (task + '.json'))
            assert config['astra_provider'] == 'zzy-astra' and config['max_model_calls'] == 400
            assert config['robodojo_root'] == str(source)
            assert config['zzy_credentials'] == str(work / 'private-key-file')
            assert config['provider_base_urls'] == {'zzy-astra': 'https://gateway.example.org/v1'}
            assert '/workspace/' not in config['zzy_credentials']
            for name in config['skill_names']:
                assert (Path(config['skills_root']) / name / 'SKILL.md').is_file()
        # Native source validation must reject even untracked source mutations.
        unexpected = source / 'unexpected.py'
        unexpected.write_text('raise RuntimeError("modified simulator")\n')
        try:
            release.source_check(source, pins)
        except ValueError as error:
            assert 'Dirty runtime source' in str(error)
        else:
            raise AssertionError('Dirty native source accepted')
        unexpected.unlink()
        # Reject traversal before writing any extracted file.
        archive = work / 'malicious.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            item = tarfile.TarInfo('../escape'); item.size = 1
            bundle.addfile(item, io.BytesIO(b'x'))
        expected = [{'path': '../escape', 'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}]
        try:
            release.extract_asset_part(archive, work / 'assets', expected)
        except ValueError as error:
            assert 'Invalid asset' in str(error)
        else:
            raise AssertionError('Archive traversal accepted')
        assert not (work / 'escape').exists()
        # A local CPU call must fail before subprocesses, including nvidia-smi/HDFS.
        with patch.dict(os.environ, {}, clear=True), patch('subprocess.check_output') as command:
            try:
                admit(work / 'missing-local.json', work)
            except RuntimeError as error:
                assert 'GPU worker' in str(error)
            else:
                raise AssertionError('Controller Pod passed worker admission')
            command.assert_not_called()
    print(json.dumps({'status': 'passed', 'tasks_prepared': len(tasks),
                      'bundles_restored_offline': 2, 'gpu_calls': 0, 'model_calls': 0}))


if __name__ == '__main__':
    main()
