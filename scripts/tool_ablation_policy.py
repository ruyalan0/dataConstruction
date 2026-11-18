"""Run sealed joint execution with zero API tools or only control_arms."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from full_tool_retry_policy import make_retrying_create, RETRY_POLICY

ALL_TOOLS = {'control_arms', 'review_frames', 'project_pixels', 'triangulate_point',
             'triangulate_temporal_point', 'verify_completion', 'stop_episode'}
MODES = {'all_tools_off': set(), 'control_only': {'control_arms'}}


def filter_context(question):
    data = json.loads(question)
    for key in ('multiview_geometry', 'temporal_geometry', 'review_capability', 'reviewed_observations'):
        data.pop(key, None)
    return json.dumps(data, allow_nan=False)


def model_system(system, mode):
    from egg_harness.skill_policy import SYSTEM, JOINT_INSTRUCTIONS
    # Preserve the original appended skill text, replacing the tool-specific policy preamble.
    from egg_harness.skill_policy import system_prompt
    original = system_prompt('joint')
    skills = system[len(original):] if system.startswith(original) else system
    basic = SYSTEM[:SYSTEM.index('Use a supplied native function tool.')]
    joint = JOINT_INSTRUCTIONS
    if mode == 'all_tools_off':
        joint = joint.replace('Use the supplied control_arms native tool for motion.',
                              'Return basic joint motion in the required JSON action fields.')
        instruction = ('All seven API tools are disabled. Return exactly one JSON decision matching the '
                       'provided text schema, with assessment, plan, phase, memory and action. '
                       'For motion action use left/right joint-arm fields, steps and settle; null arms hold. '
                       'For an incomplete attempt action may be {"stop":true,"reason":"..."}. '
                       'There is no review, projection, triangulation, completion-check or function-call tool. '
                       'Use supplied current/previous RGB, public robot state, joint semantics and actual feedback. '
                       'Completion is determined by the simulator. To inspect after returning home, use a bounded '
                       'hold action with both arms null. Preserve compact episode memory. '
                       'Skill suggestions to call disabled tools are unavailable in this experiment.')
    else:
        instruction = ('Only control_arms is supplied. The other six API tools are disabled. '
                       'Use control_arms for all motion and bounded holds; both null arms hold. '
                       'There is no review_frames, project_pixels, triangulate_point, triangulate_temporal_point, '
                       'verify_completion or stop_episode. Completion is determined by the simulator; after '
                       'satisfying requirements and home reset use bounded holds to inspect it. '
                       'Use current/previous RGB, public state, joint semantics and actual feedback. '
                       'Preserve compact episode memory. Skill suggestions to use other tools are unavailable.')
    return basic + joint + skills + '\n' + instruction


def install(mode):
    if mode not in MODES:
        raise ValueError(mode)
    from egg_harness import native_responses, native_tools
    from egg_harness.storage import check_configured_storage
    original = native_responses.NativeToolVision

    class Contract(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)

    class Motion(Contract):
        left: native_tools.JointArm | None
        right: native_tools.JointArm | None
        steps: int = Field(ge=1, le=20)
        settle: int = Field(ge=0, le=2)

    class Stop(Contract):
        stop: Literal[True]
        reason: str

    class JsonDecision(Contract):
        assessment: str
        plan: str
        phase: Literal['fine', 'transit']
        memory: str
        action: Motion | Stop

    class AblationVision(original):
        def __init__(self, config, trace):
            super().__init__(config, trace)
            assert config['transport_retry'] == RETRY_POLICY
            assert config['tool_ablation']['mode'] == mode
            self.registry.contracts = {name: contract for name, contract in self.registry.contracts.items()
                                       if name in MODES[mode]}
            self.registry.definitions = [tool for tool in self.registry.definitions if tool['name'] in MODES[mode]]
            self.previous_response_id = None
            self.client.responses.create = make_retrying_create(
                self.client.responses.create, self.trace, lambda: self.calls, check_configured_storage)
            metadata = self.trace / 'native-tools.json'
            data = json.loads(metadata.read_text())
            data.update(tools=self.registry.definitions, enabled_tools=sorted(MODES[mode]),
                        disabled_tools=sorted(ALL_TOOLS - MODES[mode]), tool_ablation=config['tool_ablation'],
                        external_transport_retry=RETRY_POLICY,
                        retry_wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        decision_format='basic_joint_json' if mode == 'all_tools_off' else 'native_function')
            metadata.write_text(json.dumps(data, indent=2) + '\n')

        def ask(self, question, images, system=None):
            question = filter_context(question)
            system = model_system(system or '', mode)
            if mode == 'control_only':
                return super().ask(question, images, system)
            import base64
            import io
            from PIL import Image
            from egg_harness.vision import failure_record
            check_configured_storage()
            if self.pending is not None:
                raise RuntimeError('Previous action has no actual feedback')
            if self.calls >= self.config['max_model_calls']:
                raise TimeoutError('Model call budget exhausted')
            context = json.loads(question)
            content = [{'type': 'input_text', 'text': question}]
            manifest = []
            for name, path in images.items():
                raw = Path(path).read_bytes()
                with Image.open(io.BytesIO(raw)) as picture:
                    mime = {'JPEG': 'image/jpeg', 'PNG': 'image/png'}[picture.format]
                    manifest.append(dict(name=name, path=str(Path(path).resolve()), size=picture.size,
                                         sha256=hashlib.sha256(raw).hexdigest()))
                content.extend([dict(type='input_text', text=name),
                                dict(type='input_image', image_url=f'data:{mime};base64,' + base64.b64encode(raw).decode(), detail='high')])
            self.last_images = {image['name']: image for image in manifest}
            observation = dict(role='user', content=content)
            self.calls += 1
            stem = self.trace / f'{self.calls:04d}'
            record = dict(system=system, question=question, images=manifest, model=self.config['model'],
                          reasoning_effort=self.config['reasoning_effort'], api='responses', **self.provider.audit(),
                          tools=[], replayed_call_id=None, replayed_response_id=self.previous_response_id,
                          history_replayed=bool(self.previous), session_id=self.session_id, step=context['step'],
                          decision_format='basic_joint_json', text_schema=JsonDecision.model_json_schema())
            stem.with_suffix('.request.json').write_text(json.dumps(record))
            started = time.monotonic()
            try:
                response = self.client.responses.create(
                    model=self.config['model'], instructions=system, input=[*self.previous, observation],
                    tools=[], tool_choice='none', parallel_tool_calls=False, store=False,
                    include=['reasoning.encrypted_content'], reasoning={'effort': self.config['reasoning_effort']},
                    text={'format': {'type': 'json_schema', 'name': 'joint_decision', 'strict': True,
                                     'schema': JsonDecision.model_json_schema()}},
                    extra_headers=self.provider.headers(self.session_id))
                raw = response.model_dump(exclude_none=True)
                stem.with_suffix('.response.json').write_text(json.dumps(raw, indent=2))
                if response.usage is not None:
                    stem.with_suffix('.cost.json').write_text(json.dumps(dict(
                        seconds=time.monotonic() - started, usage=response.usage.model_dump(),
                        model=self.config['model'], returned_model=response.model, api='responses', **self.provider.audit())))
                if response.status != 'completed' or any(item['type'] not in ('reasoning', 'message') for item in raw['output']):
                    raise ValueError('Expected a completed text response without function calls')
                decision = JsonDecision.model_validate_json(response.output_text).model_dump(exclude_none=True)
                action = decision['action']
                if not action.get('stop'):
                    arms = {arm: action[arm] for arm in ('left', 'right') if arm in action}
                    decision['action'] = {'control_arms': dict(arms=arms, steps=action['steps'], settle=action['settle'])}
                self.pending = dict(stem=stem, observation=observation, output=raw['output'], response_id=response.id)
                return decision
            except BaseException as error:
                stem.with_suffix('.failure.json').write_text(json.dumps(dict(
                    **failure_record(error, time.monotonic() - started), **self.provider.audit())))
                raise

        def observe(self, feedback, step, ended):
            if mode == 'control_only':
                return super().observe(feedback, step, ended)
            if self.pending is None:
                return
            result = dict(feedback=feedback, step=step, ended=ended)
            self.pending['stem'].with_suffix('.action-result.json').write_text(json.dumps(result, indent=2))
            self.previous = [self.pending['observation'], *self.pending['output'],
                             dict(role='user', content=[dict(type='input_text', text=json.dumps(result, allow_nan=False))])]
            self.previous_response_id = self.pending['response_id']
            self.pending = None

        def fail_pending(self, error, step):
            if mode == 'control_only':
                return super().fail_pending(error, step)
            if self.pending is not None:
                self.pending['stem'].with_suffix('.action-result.json').write_text(json.dumps(dict(
                    status='aborted', step=step, error_type=type(error).__name__, message=str(error))))
                self.pending = None

    native_responses.NativeToolVision = AblationVision
    return AblationVision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness', type=Path, required=True)
    parser.add_argument('--mode', choices=MODES, required=True)
    args, remaining = parser.parse_known_args()
    sys.path.insert(0, str(args.harness.resolve()))
    install(args.mode)
    from egg_harness import skill_policy
    sys.argv = [str(Path(__file__).resolve()), *remaining]
    skill_policy.main()


if __name__ == '__main__':
    main()
