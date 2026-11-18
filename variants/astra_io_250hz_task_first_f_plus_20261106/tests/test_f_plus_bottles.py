"""Check the isolated f+ standing-bottle guidance and unchanged f settings."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
PARENT = HERE.parent / 'astra_io_250hz_task_first_e_merged_20261105'
sys.path[:0] = [str(HERE), str(HERE / 'harness')]
import verify_originals as sources
from egg_harness.auxiliary_observation import with_observer_skill
from egg_harness.joint_goal import controller_settings
from egg_harness.prompting import TOOL_INSTRUCTIONS, build_system

SKILL = Path('harness/skills/put-bottles-into-dustbin/SKILL.md')
PHASES = ['SET SIDE POSTURE:', 'STAGE BEHIND:', 'ADVANCE:', 'CLOSE AND TEST LIFT:']


class FPlusBottleTests(unittest.TestCase):
    def test_current_f_is_frozen_and_unchanged(self):
        expected = json.loads((HERE / 'parent-f-sha256.json').read_text())
        self.assertTrue(expected)
        for filename, digest in expected.items():
            self.assertEqual(hashlib.sha256((sources.ROOT / filename).read_bytes()).hexdigest(), digest, filename)
        self.assertTrue(sources.verify()['passed'])

    def test_only_bottle_skill_changes_and_lying_guidance_is_identical(self):
        changed = []
        for original in sorted((PARENT / 'harness/skills').glob('*/SKILL.md')):
            relative = original.relative_to(PARENT)
            if original.read_bytes() != (HERE / relative).read_bytes():
                changed.append(relative)
        self.assertEqual(changed, [SKILL])
        self.assertEqual((HERE / SKILL).read_text().split('For a LYING bottle,', 1)[1],
                         (PARENT / SKILL).read_text().split('For a LYING bottle,', 1)[1]
                         .replace('stop the old descent', 'stop the current approach'))

    def test_task_configuration_changes_are_identity_only(self):
        configs = [Path('general_pickup.json')]
        configs += [filename.relative_to(PARENT) for filename in sorted((PARENT / 'task-configs').glob('*.json'))]
        for relative in configs:
            previous = json.loads((PARENT / relative).read_text())
            current = json.loads((HERE / relative).read_text())
            previous.pop('io_variant', None)
            self.assertEqual(current.pop('io_variant', None), HERE.name)
            self.assertEqual(current, previous, str(relative))

    def test_common_controller_and_all_task_limits_are_inherited(self):
        controller = Path('harness/configs/joint-goal-controller.json')
        self.assertEqual((HERE / controller).read_bytes(), (PARENT / controller).read_bytes())
        for task in ['general_pickup', 'push_T', 'put_bottles_into_dustbin',
                     'play_tic_tac_toe', 'stack_bowls', 'stack_blocks', 'classify_objects']:
            self.assertEqual(controller_settings(task)['joint_velocity_limit_rad_s'],
                             0.6 if task == 'push_T' else 3.75, task)

    def test_actual_system_contains_side_approach_and_separate_grasp_checks(self):
        config = json.loads((HERE / 'task-configs/put_bottles_into_dustbin.json').read_text())
        names = with_observer_skill(config['skill_names'])
        skills = [(name, (HERE / 'harness/skills' / name / 'SKILL.md').read_text()) for name in names]
        system = TOOL_INSTRUCTIONS + '\n' + build_system(skills)
        offsets = [system.index(phase) for phase in PHASES]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn('approach axis points horizontally toward the bottle', system)
        self.assertIn('both fingertip ends still short of its near surface and a visible gap', system)
        self.assertIn('Stop at this separated pregrasp point for an observation before advancing', system)
        self.assertIn('joint-goal interpolation does not guarantee a straight Cartesian approach', system)
        self.assertIn('close in a separate control_arms call', system)
        self.assertIn('another call for a short vertical test lift', system)
        self.assertNotIn('REORIENT ABOUT FINGER CENTER:', system)
        provenance = json.loads((HERE / 'merge-provenance.json').read_text())
        self.assertEqual(hashlib.sha256((HERE / SKILL).read_bytes()).hexdigest(),
                         provenance['changes']['put_bottles_into_dustbin']['sha256'])

    def test_drift_in_f_is_rejected(self):
        manifest = json.loads((HERE / 'parent-f-sha256.json').read_text())
        filename = next(name for name in manifest if name.endswith(str(SKILL)))
        original = sources.updated_pins

        def changed_pins(values):
            values = dict(values, **{filename: '0' * 64})
            return original(values)

        with patch.object(sources, 'updated_pins', side_effect=changed_pins):
            self.assertIn(filename, sources.verify()['differences'])
            with self.assertRaisesRegex(ValueError, 'Protected source'):
                sources.verify_release()


if __name__ == '__main__':
    unittest.main()
