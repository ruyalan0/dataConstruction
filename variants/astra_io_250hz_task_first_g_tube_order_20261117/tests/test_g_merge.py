"""Verify the g merge preserves insertion, task isolation and export contracts."""
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness')]
import export_swift
from run_local import resolve_interpolation_profile
from egg_harness.io_contract import dumps_four_decimals
from egg_harness.joint_goal import controller_settings
from egg_harness.prompting import build_system


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GMergeTests(unittest.TestCase):
    def setUp(self):
        self.provenance = json.loads((HERE / 'merge-provenance.json').read_text())
        self.merge = self.provenance['merge_20261112']
        self.previous = Path(self.merge['previous_g_snapshot'])

    def test_all_copied_files_match_exact_donor_hashes(self):
        for relative, record in self.merge['donor_files'].items():
            with self.subTest(relative=relative):
                expected = record['sha256']
                for update in ('push_t_coarse_to_fine_update', 'align_blocks_velocity_update',
                               'align_blocks_trial_push_update'):
                    revision = self.provenance.get(update, {}).get('changed_files', {}).get(relative)
                    if revision:
                        self.assertEqual(revision['previous_sha256'], expected)
                        expected = revision['sha256']
                self.assertEqual(digest(HERE / relative), expected)
                self.assertEqual(digest(ROOT / record['source']), record['sha256'])
        self.assertEqual(len(self.merge['donor_files']), 11)

    def test_recorded_task_skill_updates_preserve_unrelated_skills(self):
        before = self.previous / 'harness/skills'
        after = HERE / 'harness/skills'
        changes = []
        for source in before.rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts:
                relative = source.relative_to(before)
                if source.read_bytes() != (after / relative).read_bytes():
                    changes.append(str(relative))
        self.assertEqual(sorted(changes), ['align-blocks/SKILL.md', 'insert-tubes/SKILL.md', 'pour-liquid-into-cup/SKILL.md', 'push-t/SKILL.md',
                                           'put-bottles-into-dustbin/SKILL.md'])
        pouring = self.provenance['changes']['pour_liquid_into_cup']
        self.assertEqual(digest(before / 'pour-liquid-into-cup/SKILL.md'), pouring['previous_sha256'])
        self.assertEqual(digest(after / 'pour-liquid-into-cup/SKILL.md'), pouring['sha256'])
        alignment = self.provenance['align_blocks_trial_push_update']['changed_files'][
            'harness/skills/align-blocks/SKILL.md']
        self.assertEqual(digest(before / 'align-blocks/SKILL.md'), alignment['previous_sha256'])
        self.assertEqual(digest(after / 'align-blocks/SKILL.md'), alignment['sha256'])
        insertion = HERE / 'harness/skills/insert-tubes/SKILL.md'
        order_update = self.provenance['insert_tubes_order_update']['changed_files'][
            'harness/skills/insert-tubes/SKILL.md']
        self.assertEqual(order_update['previous_sha256'], self.merge['retained_insert_tubes_sha256'])
        self.assertEqual(digest(insertion), order_update['sha256'])

    def test_only_push_configuration_changes_and_common_controller_matches_e_plus(self):
        shared = 'harness/configs/joint-goal-controller.json'
        donor = ROOT / self.merge['bottle_source']
        self.assertEqual((HERE / shared).read_bytes(), (self.previous / shared).read_bytes())
        self.assertEqual((HERE / shared).read_bytes(), (donor / shared).read_bytes())
        for source in (self.previous / 'task-configs').glob('*.json'):
            current = HERE / 'task-configs' / source.name
            before = json.loads(source.read_text())
            after = json.loads(current.read_text())
            self.assertEqual(after.pop('io_variant'), HERE.name)
            before.pop('io_variant')
            if source.name != 'push_T.json':
                self.assertEqual(after, before, source.name)
                continue
            changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
            self.assertEqual(changed, {'goal_controller', 'interpolation_profile'})
            self.assertEqual(after['interpolation_profile'], 'quintic')
            with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='quintic'):
                self.assertEqual(after['goal_controller'], controller_settings('push_T'))

    def test_profile_defaults_follow_each_task_configuration(self):
        with patch.dict(os.environ):
            os.environ.pop('ASTRA_INTERPOLATION_PROFILE', None)
            for task, profile in self.merge['default_interpolation_profiles'].items():
                with self.subTest(task=task):
                    self.assertEqual(resolve_interpolation_profile(task), profile)
                    self.assertEqual(resolve_interpolation_profile(task, task_config=HERE / 'task-configs' / (task + '.json')), profile)
            self.assertEqual(resolve_interpolation_profile('general_pickup'), 'linear')

    def test_explicit_profile_overrides_environment_which_overrides_task_default(self):
        with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='linear'):
            self.assertEqual(resolve_interpolation_profile('push_T'), 'linear')
            self.assertEqual(resolve_interpolation_profile('push_T', 'quintic'), 'quintic')
            self.assertEqual(resolve_interpolation_profile('insert_tubes', 'native'), 'native')
        with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='invalid'):
            with self.assertRaises(ValueError):
                resolve_interpolation_profile('push_T')
        with self.assertRaises(ValueError):
            resolve_interpolation_profile('push_T', 'invalid')

    def test_bottle_prompt_uses_e_standing_grasp_and_lying_alignment(self):
        config = json.loads((HERE / 'task-configs/put_bottles_into_dustbin.json').read_text())
        system = build_system([(name, (HERE / 'harness/skills' / name / 'SKILL.md').read_text())
                               for name in config['skill_names']])
        self.assertIn('select an accessible body or neck region', system)
        self.assertIn('align the grasp channel between the two fingers parallel', system)
        self.assertIn('CLOSING direction crosses the body perpendicular', system)
        self.assertNotIn('Apply this overhead approach again when regrasping a bottle staged upright', system)

    def test_swift_argument_validation_supports_hybrid_only_for_push_quintic(self):
        arguments = dict(assessment='clear', plan='initial approach', memory='initial reach',
                         left=dict(control='joint', joints=[.1, 0., 0., 0., 0., 0.], gripper=0.),
                         right=None, motion_stage='initial_reach')
        text = dumps_four_decimals(arguments)
        export_swift.validate_arguments(text, 'push_T', 'quintic')
        for task, profile in [('insert_tubes', 'quintic'), ('swap_T', 'quintic'), ('push_T', 'linear')]:
            with self.subTest(task=task, profile=profile), self.assertRaises(ValueError):
                export_swift.validate_arguments(text, task, profile)
        del arguments['motion_stage']
        plain = dumps_four_decimals(arguments)
        export_swift.validate_arguments(plain, 'insert_tubes', 'linear')
        with self.assertRaises(ValueError):
            export_swift.validate_arguments(plain, 'push_T', 'quintic')

    def test_provenance_config_hash_and_scope_remain_consistent(self):
        config = HERE / 'task-configs/push_T.json'
        self.assertEqual(digest(config), self.provenance['task_configs']['push_T']['sha256'])
        self.assertEqual(self.provenance['variant'], HERE.name)
        self.assertFalse(self.merge['historical_experiments_modified'])
        self.assertFalse(self.merge['new_experiments_started'])
        self.assertEqual(self.provenance['changes']['push_T']['controller_update']['stage_profiles'],
                         dict(initial_reach='linear', coarse_push='linear', manipulation='quintic', home_return='linear'))


if __name__ == '__main__':
    unittest.main()
