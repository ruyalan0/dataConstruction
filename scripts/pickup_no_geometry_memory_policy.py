"""Remove all three geometry tools while retaining the previous memory ablation."""
import json

import pickup_no_triangulate_memory_policy as previous

DISABLED_TOOLS = frozenset({
    'project_pixels', 'triangulate_point', 'triangulate_temporal_point',
})
ABLATION = {
    **previous.ABLATION,
    'name': 'no_geometry_tools_or_model_memory',
    'disabled_tools': sorted(DISABLED_TOOLS),
    'joint_motion_previews': True,
    'posture_and_finger_geometry_preserved': True,
    'motion_executor_preserved': True,
    'remaining_tools': ['review_frames', 'verify_completion',
                        'stop_episode', 'control_arms'],
}


def filter_context(question):
    context = json.loads(question)
    for key in ('memory', 'multiview_geometry', 'temporal_geometry'):
        context.pop(key, None)
    context['ablation'] = ABLATION
    return json.dumps(context, allow_nan=False)


def filter_system(system):
    start = system.index('You may request action:{project:')
    end = system.index('Include compact episode memory:', start)
    system = system[:start] + system[end:]
    start = system.index('Include compact episode memory:')
    end = system.index('Never declare native success;', start)
    system = system[:start] + system[end:]
    system = system.replace('assessment, plan, phase and memory fields',
                            'assessment, plan and phase fields')
    system = system.replace('},memory:string}', '}}')
    system = system.replace(
        'head ray-plane projection requires an explicit estimated height.',
        'image-to-world projection tools are disabled in this experiment.')
    return system + (
        '\nExperiment restriction: project_pixels, triangulate_point and '
        'triangulate_temporal_point are disabled. Only review_frames, '
        'verify_completion, stop_episode and control_arms are callable. '
        'Explicit episode memory and prior model conversation replay remain '
        'disabled; do not include a memory field. This overrides skills that '
        'recommend unavailable tools or a memory ledger. Decide from supplied '
        'RGB, current robot state, joint motion previews, previous-action '
        'feedback, current/previous images and permitted frame review. '
        'Assessment, plan and phase describe only this decision.\n')


def install():
    previous.DISABLED_TOOLS = DISABLED_TOOLS
    previous.ABLATION = ABLATION
    previous.filter_context = filter_context
    previous.filter_system = filter_system
    return previous.install()


def main():
    previous.DISABLED_TOOLS = DISABLED_TOOLS
    previous.ABLATION = ABLATION
    previous.filter_context = filter_context
    previous.filter_system = filter_system
    previous.main()


if __name__ == '__main__':
    main()
