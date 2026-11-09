"""The shared entry point must preserve the recorded seven-task f experiment."""
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / 'harness'))
from egg_harness.auxiliary_observation import with_observer_skill
from egg_harness.joint_goal import controller_settings
from egg_harness.observation_pair import PROTOCOL
from egg_harness.prompting import TOOL_INSTRUCTIONS, build_system


class PortableCampaignTests(unittest.TestCase):
    def test_prompts_and_execution_settings_match_recorded_f(self):
        recorded = json.loads((ROOT / 'provenance/seven-task-equivalence.json').read_text())['tasks']
        self.assertEqual(len(recorded), 7)
        with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='linear', ASTRA_CONTROLLER_TASK=''):
            for task, expected in recorded.items():
                with self.subTest(task=task):
                    config = json.loads((HERE / 'task-configs' / (task + '.json')).read_text())
                    self.assertEqual(config['task'], task)
                    self.assertEqual(config['input_protocol'], PROTOCOL)
                    self.assertEqual(config['max_model_calls'], expected['max_model_calls'])
                    self.assertEqual(config['reasoning_effort'], 'high')
                    skills = [(name, (HERE / 'harness/skills' / name / 'SKILL.md').read_text())
                              for name in with_observer_skill(config['skill_names'])]
                    system = TOOL_INSTRUCTIONS + '\n' + build_system(skills)
                    self.assertEqual(hashlib.sha256(system.encode()).hexdigest(), expected['system_sha256'])
                    self.assertEqual(controller_settings(task)['joint_velocity_limit_rad_s'],
                                     expected['reference_velocity_limit_rad_s'])


if __name__ == '__main__':
    unittest.main()
