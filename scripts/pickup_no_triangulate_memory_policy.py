"""Disable current-view triangulation and explicit/replayed model memory."""
import argparse
import json
import sys
from pathlib import Path

DISABLED_TOOLS = frozenset({'triangulate_point'})
ABLATION = {
    'name': 'no_triangulate_point_or_model_memory',
    'disabled_tools': sorted(DISABLED_TOOLS),
    'explicit_episode_memory': False,
    'model_history_replay': False,
    'rgb_memory_enabled': False,
    'skills_preserved': True,
    'calibration_preserved': True,
    'public_robot_state_preserved': True,
    'previous_images_and_action_feedback_preserved': True,
    'remaining_tools': ['review_frames', 'project_pixels',
                        'triangulate_temporal_point', 'verify_completion',
                        'stop_episode', 'control_arms'],
}


def filter_context(question):
    context = json.loads(question)
    context.pop('memory', None)
    context.pop('multiview_geometry', None)
    context['ablation'] = ABLATION
    return json.dumps(context, allow_nan=False)


def filter_system(system):
    # Remove the unavailable operation's directions and text-memory contract.
    start = system.index('Use triangulate_point when')
    end = system.index('For temporal depth', start)
    system = system[:start] + system[end:]
    start = system.index('Include compact episode memory:')
    end = system.index('Never declare native success;', start)
    system = system[:start] + system[end:]
    system = system.replace('assessment, plan, phase and memory fields',
                            'assessment, plan and phase fields')
    system = system.replace('},memory:string}', '}}')
    return system + (
        '\nExperiment restriction: triangulate_point is disabled. All other '
        'supplied tools remain enabled, including control_arms, project_pixels '
        'and triangulate_temporal_point. Explicit episode memory and prior model '
        'conversation replay are disabled. Do not include a memory field. This '
        'overrides any skill requesting a memory ledger. Use the supplied current '
        'state, previous-action feedback, current/previous images and permitted '
        'frame review. Assessment, plan and phase describe only this decision.\n')


def install():
    from egg_harness import native_tools
    original_registry = native_tools.ToolRegistry

    class FilteredRegistry(original_registry):
        def __init__(self, config):
            super().__init__(config)
            self.contracts = {name: value for name, value in self.contracts.items()
                              if name not in DISABLED_TOOLS}
            self.definitions = [item for item in self.definitions
                                if item['name'] not in DISABLED_TOOLS]
            for item in self.definitions:
                schema = item['parameters']
                schema['properties'].pop('memory')
                schema['required'].remove('memory')

        def decision(self, name, arguments):
            data = json.loads(arguments)
            if 'memory' in data:
                raise ValueError('Episode memory is disabled')
            # The unchanged policy loop expects a string. It receives only an
            # empty placeholder that is never sent to the model or retained.
            data['memory'] = ''
            return super().decision(name, json.dumps(data, allow_nan=False))

    native_tools.ToolRegistry = FilteredRegistry
    from egg_harness import native_responses
    native_responses.TOOL_INSTRUCTIONS = (
        'Use exactly one supplied native function tool. Express the operation '
        'through its parameters, not a JSON text answer. Include assessment, '
        'plan and phase. Null optional fields hold or omit. Only enabled tools '
        'are callable. Each request is independent: prior model conversation '
        'is not replayed and no episode-memory text is supplied. Inspect the '
        'public observations and actual preceding action feedback.\n')
    original_vision = native_responses.NativeToolVision

    class FilteredVision(original_vision):
        def __init__(self, config, trace):
            super().__init__(config, trace)
            path = self.trace / 'native-tools.json'
            audit = json.loads(path.read_text())
            audit['history_policy'] = 'No prior model output replay; public action/observation feedback preserved'
            audit['ablation'] = ABLATION
            path.write_text(json.dumps(audit, indent=2) + '\n')

        def ask(self, question, images, system=None):
            # observe() still records the real execution locally, but no old
            # reasoning, tool arguments, memory or conversation is replayed.
            self.previous = []
            return super().ask(filter_context(question), images,
                               system=filter_system(system or ''))

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
