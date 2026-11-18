"""Withhold exactly the three tools used in the authorized baseline."""
import argparse
import json
import sys
from pathlib import Path

DISABLED_TOOLS = frozenset({'control_arms', 'project_pixels', 'triangulate_point'})
ABLATION = {
    'name': 'disable_three_baseline_tools',
    'disabled_tools': sorted(DISABLED_TOOLS),
    'replacement_motion_interface': False,
    'skills_preserved': True,
    'public_robot_state_preserved': True,
    'joint_previews_preserved': True,
    'remaining_tools': ['review_frames', 'triangulate_temporal_point',
                        'verify_completion', 'stop_episode'],
}


def install():
    from egg_harness import native_tools
    original_registry = native_tools.ToolRegistry

    class FilteredRegistry(original_registry):
        def __init__(self, config):
            super().__init__(config)
            self.contracts = {name: contract for name, contract in self.contracts.items()
                              if name not in DISABLED_TOOLS}
            self.definitions = [item for item in self.definitions
                                if item['name'] not in DISABLED_TOOLS]

    native_tools.ToolRegistry = FilteredRegistry
    from egg_harness import native_responses
    original_vision = native_responses.NativeToolVision

    class FilteredVision(original_vision):
        def ask(self, question, images, system=None):
            context = json.loads(question)
            context['ablation'] = ABLATION
            # Preserve the baseline state/skills; explicitly override stale tool
            # recommendations instead of introducing a substitute action path.
            notice = ('\nExperiment restriction: control_arms, project_pixels and '
                      'triangulate_point are DISABLED. This restriction overrides '
                      'any instructions or skills recommending these tools. Only '
                      'the supplied function definitions are callable. There is '
                      'no replacement motion interface. review_frames and '
                      'triangulate_temporal_point do not move the robot; '
                      'verify_completion only advances hold steps. If the task '
                      'cannot proceed with the available tools, use stop_episode '
                      'and explain the missing capability.\n')
            return super().ask(json.dumps(context), images,
                               system=(system or '') + notice)

    native_responses.NativeToolVision = FilteredVision
    return FilteredRegistry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness-root', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    sys.path.insert(0, str(args.harness_root.resolve()))
    install()
    from egg_harness import skill_policy
    sys.argv = [str(Path(__file__).resolve()), *remaining]
    skill_policy.main()


if __name__ == '__main__':
    main()
