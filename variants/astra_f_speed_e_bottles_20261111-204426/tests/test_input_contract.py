"""Preserve planning information while removing duplicate presentation fields."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness')]
from egg_harness.input_contract import (compact_budget, compact_joint_semantics,
    compact_motion, compact_task_requirements, strip_schema_titles)
from egg_harness.joint_control import joint_semantics
from egg_harness.kinematics import Kinematics
from egg_harness.prompting import build_system, skill_body
from test_transport import state, context
from egg_harness.observation_pair import build_pair, CAMERAS
from egg_harness.io_contract import round_numbers


class InputContractTests(unittest.TestCase):
    def test_shared_definitions_reconstruct_all_original_fk_information_exactly(self):
        model = Kinematics(json.loads((HERE / 'harness/configs/x5-kinematics.json').read_text()))
        measured = state(.123456789012)
        measured['right_arm_joint_state'][5] = 3.13
        source = joint_semantics(model, measured)
        before = deepcopy(source)
        compact = compact_joint_semantics(source, measured)
        self.assertEqual(source, before)
        self.assertEqual(len(compact['joint_definitions']), 6)
        for arm in ('left', 'right'):
            for index, record in enumerate(compact['arms'][arm]):
                expanded = deepcopy(record)
                expanded.update(compact['joint_definitions'][record['control_name']])
                expanded.update(angle_rad=measured[arm+'_arm_joint_state'][index], urdf_name=record['control_name'])
                self.assertEqual(expanded, source['arms'][arm][index])
        self.assertFalse(compact['arms']['right'][5]['preview']['positive']['available'])

    def test_nonidentical_arm_definitions_and_urdf_names_are_preserved(self):
        source = context()['joint_semantics']
        source['arms']['right'][0]['limits_rad'] = [-9., 9.]
        source['arms']['right'][0]['urdf_name'] = 'right_base'
        result = compact_joint_semantics(source, state(0.))
        self.assertNotIn('joint_definitions', result)
        self.assertEqual(result['arms']['right'][0]['limits_rad'], [-9., 9.])
        self.assertEqual(result['arms']['right'][0]['urdf_name'], 'right_base')

    def test_stale_joint_measurements_are_not_silently_removed(self):
        source = context()['joint_semantics']
        source['arms']['left'][0]['angle_rad'] = .5
        with self.assertRaisesRegex(ValueError, 'stale'):
            compact_joint_semantics(source, state(0.))

    def test_public_task_completion_survives_metadata_and_exact_instruction_dedup(self):
        source = dict(task='fold_clothes', url='source', sha256='hash', fetched_at='date', rows=[
            ['Instruction', 'Fold the clothes neatly.'], ['Platform', 'Simulation'],
            ['Description', 'Fold both sleeves and hem.'], ['Completion', 'Fold and return home.']])
        original = deepcopy(source)
        result = compact_task_requirements(source, 'Fold the clothes neatly.')
        self.assertEqual(source, original)
        self.assertEqual(result, dict(task='fold_clothes', rows=[
            ['Description', 'Fold both sleeves and hem.'], ['Completion', 'Fold and return home.']]))
        different = compact_task_requirements(source, 'A different runtime instruction')
        self.assertIn(source['rows'][0], different['rows'])

    def test_seconds_use_actual_control_ticks_not_the_25hz_task_counter(self):
        result = compact_budget(dict(task_horizon_seconds=8., model_calls_remaining=123,
            remaining_native_steps=192, elapsed_simulation_seconds=.32), 83, 250)
        self.assertEqual(result, dict(remaining_simulation_seconds=8.-.332, model_calls_remaining=123))
        with self.assertRaises(ValueError):
            compact_budget(dict(task_horizon_seconds=8., model_calls_remaining=0), 2001, 250)

    def test_motion_summary_keeps_failures_and_errors_without_repeated_time_or_false_claims(self):
        feedback = dict(status='gripper_settled_before_target', reason=None, executed_steps=83,
            actual_duration_seconds=.332, target_reached=False, joint_targets_reached=True,
            gripper_settled=True, final_joint_error_rad={'left':.002,'right':.001},
            gripper_error_m={'left':.02,'right':0.}, tracking=['do not send'], success=True)
        self.assertEqual(compact_motion(feedback), dict(status='gripper_settled_before_target',
            final_joint_error_rad=feedback['final_joint_error_rad'], gripper_error_m=feedback['gripper_error_m']))
        self.assertEqual(compact_motion(dict(status='rejected_before_motion', reason='bad target')),
                         dict(status='rejected_before_motion', reason='bad target'))

    def test_schema_titles_are_removed_without_removing_a_real_title_property(self):
        source = dict(title='Root', type='object', required=['title'], additionalProperties=False,
            properties={'title':dict(type='string', title='Field'), 'angle':dict(type='number', minimum=-1, maximum=1)})
        result = strip_schema_titles(source)
        self.assertIn('title', result['properties'])
        self.assertEqual(result['required'], ['title'])
        self.assertFalse(result['additionalProperties'])
        self.assertEqual(result['properties']['angle'], source['properties']['angle'])

    def test_skill_authoring_metadata_is_not_model_instruction(self):
        text = '---\nname: test\ndescription: author\n---\n\nKeep this strategy.\n\nEvidence status: archive.\n\nKeep this public finish.'
        self.assertEqual(skill_body(text), 'Keep this strategy.\n\nKeep this public finish.')
        with self.assertRaisesRegex(ValueError, 'Unclosed'):
            skill_body('---\nname: broken')

    def test_presentation_rounds_measurements_without_changing_sources_images_or_memory(self):
        source = context(83, .123456789012, 'essential memory')
        original = deepcopy(source)
        images = {name:name+'.jpg' for name in CAMERAS}
        result, views, _ = build_pair(source, images)
        self.assertEqual(source, original)
        self.assertEqual(result['observation']['state'], round_numbers(source['state']))
        self.assertEqual(result['observation']['memory'], 'essential memory')
        self.assertEqual(list(views.values()), list(images.values()))
        self.assertNotIn('control_timing', result)
        self.assertNotIn('action_interface', result)
        self.assertIn('held_gripper_change_guard_m', result['controller'])

    def test_loaded_task_guidance_has_no_removed_tools_or_duration_commands(self):
        names = ['observation-grounding','visual-contact-control','upright-grasp-approach',
                 'transport-retention','general-pickup','pour-liquid-into-cup','imitate-sorting-sequence']
        system = build_system([(name,(HERE/'harness/skills'/name/'SKILL.md').read_text()) for name in names])
        for phrase in ['move_local', 'action:{verify:true}', 'increase the duration', '35 mm',
                       'one or two native steps', 'legacy direct-posture tool', 'omit the gripper field',
                       'Evidence status:', 'configs/public-tasks.json']:
            self.assertNotIn(phrase, system)
        self.assertIn('10 cm', system)
        self.assertIn('This task requires neither placement nor home return.', system)

    def test_all_selectable_skills_avoid_unavailable_control_syntax(self):
        disabled = {'direct-posture-adjustment', 'multijoint-escape', 'folded-right-recovery-case', 'robot-fine-control'}
        phrases = ['triangulate_point', 'review_frames', 'action:{verify', 'phase:fine',
                   'steps:', 'settle:', 'local IK', '35mm', 'one native step',
                   'public history review', 'supplied review action']
        for path in sorted((HERE/'harness/skills').glob('*/SKILL.md')):
            if path.parent.name in disabled:
                continue
            body = skill_body(path.read_text())
            for phrase in phrases:
                self.assertNotIn(phrase, body, f'{path.parent.name}: {phrase}')


if __name__ == '__main__':
    unittest.main()
