import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .arx_x5_interpreter import Decision, rotation
from .azure_session import ChatSession, JsonSession
from .evidence_tools import SCHEMAS, EvidenceTools, ToolInputError
from .executor import Executor
from .observation_adapter import adapt
from .public_instructions import public_instruction
from .trace_store import TraceStore
from .trajectory import Trajectory


class Recall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sequence: int = Field(ge=0, strict=True)
    camera: str = Field(min_length=1)

SYSTEM = """Control the dual ARX X5 through act. Use only supplied RGB and robot state.
Pose = [x,y,z,qw,qx,qy,qz], meters, environment-local world axes, z up.
world means the environment world orientation, NOT either robot base or camera axes.
tool means the observed end-link's local orientation, NOT calibrated grasp axes.
translate amount is millimeters, rotate amount is degrees (right-hand rule).
Translate at most 30 mm per decision. Rotate at most 15 degrees per decision.
Both arms must be specified; hold preserves the observed pose and last gripper command.
Gripper open=1, close=0. Gripper command echo is NOT measured width or contact.
steps specifies how many native control calls repeat this absolute target (1..8).
For a diagonal or combined translation/rotation, use trajectory with 1..4 waypoints.
Every waypoint offset is relative to the decision-start pose (or the explicit latched
reference), never an increment from the preceding waypoint. Total per-arm path remains
bounded to 30 mm / 15 degrees. motion_steps samples the EE path with quaternion SLERP;
settle_steps repeats its endpoint, including gripper waiting. Their sum is at most 8.
Each waypoint is visited; its gripper command starts on that waypoint's segment.
Review occurs after settle or a measured tracking interruption, with each native observation
archived. Pose reached does not skip settling. Sustained low progress reports pose evidence,
not a diagnosis of collision, IK failure or contact. Reassess before resending a blocked move.
hold and hold_current hold the latest observed pose. hold_latched uses a persistent pose
initially set to the episode-initial pose; latch explicitly replaces it with the current pose.
Latched references concern link6 pose, not a calibrated TCP or object grasp point.
Use small motions near contact. Examine actual displacement and images before declaring
progress. Pose reached does not prove grasp, release or task success. Keep evidence
references and unconfirmed hypotheses in note. recall retrieves a previous original RGB.
Never infer contact merely from absent movement. All movement consumes official budget.
tool_axes_world gives the current link6 +x/+y/+z unit vectors in world coordinates,
computed only from your observed quaternion. A tool +axis translation follows that vector.
A world rotation pre-multiplies orientation; a tool rotation post-multiplies it.
Rotation pivots at link6 and holds its position, so fingers can sweep an arc.
These are link axes, not measured finger approach/jaw axes: do not assume tool +z
points out of the gripper or that world -x universally pitches downward. Use the
supplied basis vectors and wrist/head RGB to choose the required orientation.
Do not rotate to a generic vertical pose unless the visible task requires it.
Each wrist camera moves with its arm. When depth, alignment, contact or support
is unclear or occluded, consider using a free arm as a movable camera to obtain a
side or oblique view while holding the manipulating arm stable when feasible.
Choose a small, purposeful viewpoint change; compare new RGB across cameras and
before/after motion for parallax. Image overlap alone does not establish support.
Keep the camera arm clear of objects and the other arm, including its finger sweep.
Do not assume the camera optical axis equals a tool axis; verify the actual view.
Use viewpoint moves when they can resolve uncertainty, and plan the camera arm's
return too when the task requires returning to the initial poses.
When supplied, public_task_requirements supplements the short instruction with
reviewed official public task requirements; it is not a report of current success.
For tasks requiring return to origin, use both arms' supplied initial end-link poses,
not joint angles. Plan release, clearance and return yourself from RGB and robot state.
Do not assume unlisted tasks require a return to origin.
Only a bounded window of RGB is sent each request. Earlier image labels remain as
references, not visible evidence; use recall to inspect their original pixels again.
Use inspect for several frames/cameras or original-pixel crops; before_after makes
labelled paired panels. Camera motion also causes parallax; enlarged pixels add no geometry.
Use history to query old actions, notes and feedback without loading images.
Use remember to maintain a bounded subgoal and model-assessed claims with evidence_refs.
Retire or supersede contradicted claims. Only current_working_state lists active claims;
historical notes remain archived and are not automatically carried forward as beliefs.
Reference existence does not validate a claim. Repeated hold/recall may add no evidence:
consider a focused inspect or a small observation motion when it can resolve uncertainty.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "act",
            "description": "Joint dual-arm motion with an observation checkpoint",
            "parameters": Decision.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Read an original image from this episode",
            "parameters": {
                "type": "object",
                "properties": {"sequence": {"type": "integer", "minimum": 0}, "camera": {"type": "string"}},
                "required": ["sequence", "camera"],
                "additionalProperties": False,
            },
        },
    },
]
TOOLS += [{'type': 'function', 'function': {'name': name,
          'description': {'inspect': 'Compare archived original RGB with labelled panels and optional crops',
                          'remember': 'Update model-assessed working hypotheses; no task truth is checked',
                          'history': 'Read archived text/actions by event cursor, without image pixels'}[name],
          'parameters': schema.model_json_schema()}} for name, schema in SCHEMAS.items()]
TOOLS.append({'type': 'function', 'function': {'name': 'trajectory',
             'description': 'Bounded diagonal/pose motions or short waypoint paths with explicit settling',
             'parameters': Trajectory.model_json_schema()}})


class Agent:
    def __init__(
        self, client, trace_root, model="gpt-6-astra", max_calls=None, protocol="tools", image_budget=6, pin_current=False, report_steps=False, detailed_feedback=False, compact_evidence=False, max_tool_repairs=3, context_bytes=65536, ledger_bytes=8192, stall_boundaries=3
    ):
        if protocol not in {"tools", "json-high"}:
            raise ValueError("Unknown session protocol")
        self.protocol = protocol
        self.client, self.trace_root, self.model = client, trace_root, model
        self.max_calls = max_calls
        if max_calls is not None and max_calls < 1:
            raise ValueError("max_calls must be positive or None")
        if image_budget < 3:
            raise ValueError("Image budget must fit the three current cameras")
        self.image_budget = image_budget
        self.pin_current = pin_current
        self.report_steps = report_steps
        self.detailed_feedback = detailed_feedback
        if compact_evidence and protocol != "json-high":
            raise ValueError("Evidence compaction requires JSON protocol")
        self.compact_evidence = compact_evidence
        if type(max_tool_repairs) is not int or not 0 <= max_tool_repairs <= 10:
            raise ValueError('Tool repair limit must be between 0 and 10')
        self.max_tool_repairs = max_tool_repairs
        self.context_bytes, self.ledger_bytes = context_bytes, ledger_bytes
        if type(stall_boundaries) is not int or not 2 <= stall_boundaries <= 8:
            raise ValueError('stall_boundaries must be an integer between 2 and 8')
        self.stall_boundaries = stall_boundaries
        self.effective_config = {'model': model, 'protocol': protocol, 'image_budget': image_budget,
                                 'pin_current': pin_current, 'report_steps': report_steps,
                                 'detailed_feedback': detailed_feedback, 'compact_evidence': compact_evidence,
                                 'max_tool_repairs': max_tool_repairs, 'context_bytes': context_bytes,
                                 'ledger_bytes': ledger_bytes}
        self.effective_config['stall_boundaries'] = stall_boundaries
        self.reset()

    def reset(self):
        self.ended = False
        self.episode = uuid4().hex
        self.trace = TraceStore(self.trace_root, self.episode)
        self.evidence = EvidenceTools(self.trace)
        self.tools = copy.deepcopy(TOOLS)
        session_class = ChatSession if self.protocol == "tools" else JsonSession
        self.session = session_class(
            self.client, self.model, SYSTEM, self.trace, image_budget=self.image_budget
        )
        self.session.compact_evidence = self.compact_evidence
        self.session.context_bytes = self.context_bytes
        self.session.ledger_bytes = self.ledger_bytes
        self.executor = Executor(detailed_feedback=self.detailed_feedback, stall_boundaries=self.stall_boundaries)
        self.sequence = 0
        self.pending_call = None
        self.initial_poses = None
        self.executed_steps = 0
        self.requested_action = False
        self.instruction_contract = None
        self.episode_instruction = None
        self.freeze_config()

    def freeze_config(self):
        from .config import digest

        sources = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
        config = {'config': self.effective_config, 'system_sha256': digest(SYSTEM), 'tools_sha256': digest(self.tools),
                  'working_memory_schema_sha256': digest(SCHEMAS['remember'].model_json_schema()),
                  'source_tree_sha256': digest(sources), 'source_files': sources,
                  'tcp_pivot_enabled': False,
                  'native_termination': True}
        (self.trace.root / 'harness-config.json').write_text(json.dumps(config, indent=2) + '\n')

    def update_obs(self, raw, terminated=False):
        if type(terminated) is not bool:
            raise ValueError('Termination boundary must be a boolean')
        if self.ended:
            raise RuntimeError('Episode already ended')
        obs = adapt(raw, self.sequence)
        if self.episode_instruction is None:
            self.tools[1]['function']['parameters']['properties']['camera']['enum'] = list(obs.images)
            self.freeze_config()
            self.episode_instruction = obs.instruction
            self.instruction_contract = public_instruction(obs.instruction)
            self.trace.event("public_instruction_contract", {
                "instruction": obs.instruction,
                "contract": self.instruction_contract.as_message() if self.instruction_contract else None,
                "source_sha256": self.instruction_contract.source_sha256 if self.instruction_contract else None,
            })
        elif obs.instruction != self.episode_instruction:
            raise ValueError("Task instruction changed within the episode")
        if self.initial_poses is None:
            self.initial_poses = {arm: pose.tolist() for arm, pose in obs.poses.items()}
        self.trace.observation(obs)
        self.executor.observe(obs, terminated=terminated)
        if self.requested_action:
            self.executed_steps += 1
            self.requested_action = False
            self.trace.event('operation', {'id': self.pending_call, 'state': 'observed',
                                         'sequence': obs.sequence})
            self.trace.event('native_feedback', self.executor.feedback)
        if self.pending_call and self.executor.remaining == 0:
            self.session.result(self.pending_call, self.executor.feedback)
            self.trace.event("feedback", self.executor.feedback)
            self.trace.event('operation', {'id': self.pending_call, 'state': 'completed'})
            self.pending_call = None
        self.sequence += 1

    def finish(self):
        if self.ended:
            raise RuntimeError('Episode already ended')
        self.executor.finish()
        if self.pending_call:
            self.session.result(self.pending_call, self.executor.feedback)
            self.trace.event('feedback', self.executor.feedback)
            self.trace.event('operation', {'id': self.pending_call, 'state': 'completed',
                                         'outcome': 'terminated_by_environment'})
            self.pending_call = None
        self.ended = True
        self.trace.event('episode_end', {'sequence': self.executor.obs.sequence})
        return {'feedback': self.executor.feedback, 'calls': self.session.calls,
                'remaining': 0, 'episode': self.episode}

    def get_action(self):
        if self.ended:
            raise RuntimeError('Episode already ended')
        ex = self.executor
        if ex.obs is None or ex.pending:
            raise RuntimeError("update_obs required before get_action")
        if ex.remaining == 0:
            obs = ex.obs
            summary = {
                "instruction": obs.instruction,
                "frame": obs.sequence,
                "poses": {a: p.tolist() for a, p in obs.poses.items()},
                "initial_poses": self.initial_poses,
                "tool_axes_world": {
                    a: {
                        axis: rotation(p).as_matrix()[:, i].round(6).tolist()
                        for i, axis in enumerate(("+x", "+y", "+z"))
                    }
                    for a, p in obs.poses.items()
                },
                "gripper_command_echo": obs.gripper_commands,
                "feedback": ex.feedback,
                "available_frames": {"first": 0, "last": obs.sequence, "cameras": list(obs.images)},
                'camera_dimensions': {name: [im.shape[1], im.shape[0]] for name, im in obs.images.items()},
            }
            if self.compact_evidence:
                summary["evidence_check"] = (
                    "Compare the previous action's before/after frames before updating your hypotheses. "
                    "In note distinguish visually supported changes, unresolved hypotheses and contradicted "
                    "earlier claims, with frame references. Reconsider unresolved claims before calling the "
                    "task complete. Select the next action yourself; holding can be appropriate."
                )
            if self.instruction_contract is not None:
                summary["public_task_requirements"] = self.instruction_contract.as_message()
            summary['public_task_requirements_status'] = 'reviewed' if self.instruction_contract else 'not_reviewed'
            if self.report_steps:
                summary["own_executed_environment_steps"] = self.executed_steps
            content = [{"type": "text", "text": json.dumps(summary)}]
            for camera in obs.images:
                content += [
                    {"type": "text", "text": f"frame {obs.sequence}, camera {camera}"},
                    self.trace.image_content(obs.sequence, camera),
                ]
            if self.pin_current:
                self.session.protected_images = [p for p in content if p["type"] == "image_url"]
            current_images = list(self.session.protected_images)
            self.session.priority_images = []
            if self.pin_current and ex.feedback is not None:
                before = ex.feedback['before']
                content.append({'type': 'text', 'text': json.dumps({
                    'previous_action_before': before, 'current_after': obs.sequence,
                    'observed_robot_state_before': self.trace.frame_states[before],
                    'meaning': 'Archived action-start views for comparison; wrist viewpoints can move.'})})
                for camera in obs.images:
                    image = self.trace.image_content(before, camera)
                    content.extend([{'type': 'text', 'text': f'action before frame {before}, camera {camera}'}, image])
                    self.session.priority_images.append(image)
            repairs = 0
            while True:
                if self.max_calls is not None and self.session.calls >= self.max_calls:
                    raise RuntimeError("Episode model-call budget exhausted")
                self.session.working_state = self.evidence.snapshot()
                call_id, name, arguments = self.session.request(content, self.tools)
                self.trace.event('operation', {'id': call_id, 'name': name, 'state': 'prepared'})
                # Only reject input here. Executor/transport/observation errors remain fatal.
                try:
                    if name in {'act', 'trajectory'}:
                        decision = (Decision if name == 'act' else Trajectory).model_validate(arguments)
                        plan = ex.plan(decision)
                    elif name == 'recall':
                        recall = Recall.model_validate(arguments)
                        if recall.sequence not in self.trace.frames:
                            raise KeyError(f'Frame {recall.sequence} unavailable; valid frames 0..{obs.sequence}')
                        if recall.camera not in self.trace.frames[recall.sequence]:
                            raise KeyError(f'Camera unavailable; choose {list(obs.images)}')
                        image = self.trace.image_content(recall.sequence, recall.camera)
                        extra_view = image['image_url']['url'] not in {p['image_url']['url'] for p in current_images}
                        if extra_view and len(current_images) >= self.image_budget:
                            raise ToolInputError('No historical image slot beyond protected current views')
                    elif name in SCHEMAS:
                        command = self.evidence.validate(name, arguments)
                        if name == 'inspect' and len(command.cameras) + len(current_images) > self.image_budget:
                            raise ToolInputError('Requested camera panels exceed free image slots')
                        self.trace.event('operation', {'id': call_id, 'state': 'validated'})
                        result, images = self.evidence.execute(name, command)
                    else:
                        raise ValueError('Unsupported tool envelope')
                except (ValidationError, KeyError, ToolInputError) as error:
                    result = {'ok': False, 'error': {'code': 'tool_validation_error', 'message': str(error)},
                              'physical_actions_executed': 0, 'observation_sequence': obs.sequence,
                              'repair_attempts_remaining': max(0, self.max_tool_repairs - repairs)}
                    self.session.result(call_id, result)
                    self.trace.event('tool_error', {'operation_id': call_id, **result})
                    self.trace.event('operation', {'id': call_id, 'state': 'completed',
                                                 'outcome': 'validation_error'})
                    if repairs >= self.max_tool_repairs:
                        raise RuntimeError('Tool repair attempts exhausted') from error
                    repairs += 1
                    content = [{'type': 'text', 'text': 'Correct the rejected tool arguments using the same '
                                'observation. No physical action was dispatched.'}]
                    continue
                if name in SCHEMAS:
                    self.session.result(call_id, result)
                    self.trace.event('operation', {'id': call_id, 'state': 'completed'})
                    self.session.protected_images = current_images + images
                    content = [{'type': 'text', 'text': json.dumps(result)}, *images]
                    continue
                self.trace.event('operation', {'id': call_id, 'state': 'validated'})
                if name in {'act', 'trajectory'}:
                    ex.load(decision, plan)
                    self.pending_call = call_id
                    self.trace.event("decision", decision.model_dump())
                    break
                self.session.protected_images = current_images + ([image] if extra_view else [])
                self.session.result(call_id, arguments)
                self.trace.event('operation', {'id': call_id, 'state': 'completed'})
                recalled = dict(arguments)
                if self.pin_current:
                    recalled["observed_robot_state"] = self.trace.frame_states[arguments["sequence"]]
                content = [{"type": "text", "text": json.dumps(recalled)}, image]
        action = ex.next_action()
        self.requested_action = True
        self.trace.event("native_action", {k: v.tolist() for k, v in action.items()})
        self.trace.event('operation', {'id': self.pending_call, 'state': 'dispatched',
                                     'native_step': self.executed_steps + 1})
        return [action]
