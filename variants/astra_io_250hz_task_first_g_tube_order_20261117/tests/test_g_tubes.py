"""Keep the g insertion gate, exact parent pins and unrelated task skills intact."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
PARENT = ROOT / 'variants/astra_io_250hz_task_first_e_merged_20261105'
BASE_CONFIG = ROOT / 'runs/sol61-f-additional-seven-tasks-L20-L22-20261109-215604/configs/sol/linear/insert_tubes.json'
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'harness'))
import verify_originals as sources
from egg_harness.prompting import build_system


class GTubeSkillTests(unittest.TestCase):
    def test_unrelated_e_update_remains_exactly_pinned(self):
        name = 'variants/astra_io_250hz_task_first_e/harness/skills/put-bottles-into-dustbin/SKILL.md'
        updates = sources.source_updates()
        self.assertEqual(updates[name]['sha256'], hashlib.sha256((ROOT / name).read_bytes()).hexdigest())
        updates[name]['sha256'] = '0' * 64
        with patch.object(sources, 'source_updates', return_value=updates):
            self.assertIn(name, sources.verify()['differences'])

    def test_parent_f_is_pinned_and_unchanged(self):
        hashes = json.loads((HERE / 'parent-f-sha256.json').read_text())
        actual = {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in PARENT.rglob('*')
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'
        }
        self.assertEqual(actual, hashes)
        self.assertTrue(sources.verify()['passed'])

    def test_parent_f_runtime_drift_is_rejected(self):
        name = str((PARENT / 'run_local.py').relative_to(ROOT))
        original = sources.updated_pins

        def changed_pins(values):
            values = original(values)
            values[name] = '0' * 64
            return values

        with patch.object(sources, 'updated_pins', side_effect=changed_pins):
            self.assertIn(name, sources.verify()['differences'])

    def test_only_five_revised_task_skills_change(self):
        parent_skills = PARENT / 'harness/skills'
        for parent in parent_skills.rglob('*'):
            if not parent.is_file() or '__pycache__' in parent.parts:
                continue
            relative = parent.relative_to(parent_skills)
            current = HERE / 'harness/skills' / relative
            if str(relative) in {'align-blocks/SKILL.md', 'insert-tubes/SKILL.md', 'pour-liquid-into-cup/SKILL.md',
                                 'push-t/SKILL.md',
                                 'put-bottles-into-dustbin/SKILL.md'}:
                self.assertNotEqual(current.read_bytes(), parent.read_bytes())
            else:
                self.assertEqual(current.read_bytes(), parent.read_bytes(), str(relative))

    def test_f_insertion_and_completion_guidance_is_retained(self):
        relative = 'harness/skills/insert-tubes/SKILL.md'
        original = (PARENT / relative).read_text()
        current = (HERE / relative).read_text()
        marker = '\nChoose an unoccupied opening'
        prefix, suffix = original.split(marker, 1)
        self.assertTrue(current.startswith(prefix))
        self.assertTrue(current.endswith(marker + suffix))

    def test_angle_gate_is_loaded_into_the_actual_system_prompt(self):
        config = json.loads((HERE / 'task-configs/insert_tubes.json').read_text())
        skills = [
            (name, (HERE / 'harness/skills' / name / 'SKILL.md').read_text())
            for name in config['skill_names']
        ]
        system = build_system(skills)
        for required in (
            'wxyz', 't = R_tube * [0, 1, 0]', 'a = R_wrist * [1, 0, 0]',
            'c = R_wrist * [0, 1, 0]', '90 - theta', 'abs(dot(a, t))',
            '10 degrees', 'not an evaluator threshold',
            'one further distinct, settled observation',
            'at most one supported regrasp per tube',
            'cannot correct their relative grasp angle',
            'Never drop a tube', 'remaining simulation budget',
            'not already released rack-supported tubes',
        ):
            self.assertIn(required, system)

    def test_task_config_changes_only_scheme_identity(self):
        original = json.loads(BASE_CONFIG.read_text())
        current = json.loads((HERE / 'task-configs/insert_tubes.json').read_text())
        self.assertEqual(current.pop('io_variant'), HERE.name)
        original.pop('io_variant')
        self.assertEqual(current, original)
        self.assertEqual(current['goal_controller']['joint_velocity_limit_rad_s'], 3.75)
        self.assertEqual(current['max_model_calls'], 400)
        self.assertEqual(current['tool_ablation']['enabled_tools'], ['control_arms'])

    def test_g_identity_is_used_by_launcher_policy_and_exporter(self):
        for name in ('run_local.py', 'policy.py', 'export_swift.py'):
            content = (HERE / name).read_text()
            self.assertIn(HERE.name, content)
            self.assertNotIn("io_variant='" + PARENT.name + "'", content)
        provenance = json.loads((HERE / 'merge-provenance.json').read_text())
        self.assertEqual(provenance['variant'], HERE.name)
        gate = provenance['changes']['insert_tubes']
        skill = HERE / gate['skill']
        self.assertEqual(gate['sha256'], hashlib.sha256(skill.read_bytes()).hexdigest())
        self.assertFalse(gate['simulation_evaluated'])

    def test_order_update_changes_no_other_harness_files(self):
        provenance = json.loads((HERE / 'merge-provenance.json').read_text())
        update = provenance['insert_tubes_order_update']
        original = ROOT / update['source_variant'] / 'harness'
        current = HERE / 'harness'
        original_hashes = {
            str(path.relative_to(original)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in original.rglob('*')
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'
        }
        current_hashes = {
            str(path.relative_to(current)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in current.rglob('*')
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'
        }
        self.assertEqual(original_hashes.keys(), current_hashes.keys())
        changed = [name for name in original_hashes if original_hashes[name] != current_hashes[name]]
        self.assertEqual(changed, ['skills/insert-tubes/SKILL.md'])
        record = update['changed_files']['harness/skills/insert-tubes/SKILL.md']
        self.assertEqual(record['previous_sha256'], original_hashes[changed[0]])
        self.assertEqual(record['sha256'], current_hashes[changed[0]])

    def test_order_update_preserves_all_existing_task_settings(self):
        provenance = json.loads((HERE / 'merge-provenance.json').read_text())
        original = ROOT / provenance['insert_tubes_order_update']['source_variant']
        configs = list((original / 'task-configs').glob('*.json')) + [original / 'general_pickup.json']
        for source in configs:
            relative = source.relative_to(original)
            with self.subTest(config=str(relative)):
                before = json.loads(source.read_text())
                after = json.loads((HERE / relative).read_text())
                self.assertEqual(before.pop('io_variant'), original.name)
                self.assertEqual(after.pop('io_variant'), HERE.name)
                self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
