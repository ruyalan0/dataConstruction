"""Run the sealed policy with geometry tools and joint previews withheld in memory."""
import argparse
import json
import sys
from pathlib import Path

DISABLED_TOOLS = frozenset({
    'project_pixels', 'triangulate_point', 'triangulate_temporal_point',
})
ABLATION = {
    'name': 'no_geometry_tools_or_joint_previews',
    'disabled_tools': sorted(DISABLED_TOOLS),
    'joint_motion_previews': False,
    'skills_preserved': True,
    'public_robot_state_preserved': True,
    'posture_and_finger_geometry_preserved': True,
    'motion_executor_preserved': True,
}


def filter_context(question):
    context = json.loads(question)
    for key in ('multiview_geometry', 'temporal_geometry'):
        context.pop(key, None)
    for joints in context.get('joint_semantics', {}).get('arms', {}).values():
        for joint in joints:
            joint.pop('preview', None)
    context.get('joint_semantics', {}).pop('preview_delta_rad', None)
    context['ablation'] = ABLATION
    return json.dumps(context, allow_nan=False)


def filter_system(system):
    start = system.index('You may request action:{project:')
    end = system.index('Include compact episode memory:', start)
    system = system[:start] + system[end:]
    system = system.replace(
        'head ray-plane projection requires an explicit estimated height.',
        'image-to-world projection tools are disabled in this experiment.')
    return (system + '\nGeometry ablation: project_pixels, triangulate_point and '
        'triangulate_temporal_point are unavailable. Automatic positive/negative '
        'joint motion previews are withheld. Infer target geometry from RGB and '
        'measured robot state; inspect actual results after bounded joint actions.\n')


def install():
    from egg_harness import native_tools
    original_registry = native_tools.ToolRegistry

    class AblatedRegistry(original_registry):
        def __init__(self, config):
            super().__init__(config)
            self.contracts = {name: value for name, value in self.contracts.items()
                              if name not in DISABLED_TOOLS}
            self.definitions = [item for item in self.definitions
                                if item['name'] not in DISABLED_TOOLS]

    native_tools.ToolRegistry = AblatedRegistry
    from egg_harness import native_responses
    original_vision = native_responses.NativeToolVision

    class AblatedVision(original_vision):
        def ask(self, question, images, system=None):
            return super().ask(filter_context(question), images,
                               system=filter_system(system or ''))

    native_responses.NativeToolVision = AblatedVision
    return AblatedRegistry


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
