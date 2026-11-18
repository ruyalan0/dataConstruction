"""New control-only entry point; all changes are confined to this variant directory."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
HARNESS = HERE / 'harness'
sys.path.insert(0, str(HERE.parents[1] / 'scripts'))
from full_tool_retry_policy import RETRY_POLICY, make_retrying_create

JOINT_SYSTEM = '''You are Astra controlling a bimanual ARX-5 robot in RoboDojo.
Use only supplied RGB, public robot state and task skills. Only control_arms is
available. Decide the next bounded joint movement or hold from current evidence.
Return assessment, plan, updated memory, left, right, steps and settle. Each arm
is null (hold) or has control="joint", six absolute joint targets in radians or
null to hold, and gripper in [0,1] or null to retain its command. Both arms and
grippers can change together, including while carrying an object. Do not emit
a phase field. steps is 1..2500 (at most 10 seconds) and settle is 0..2, both in 250 Hz action intervals
(4 milliseconds each). A segment is not a 250 Hz model inference loop: the model
chooses a segment, and the executor issues its actions at this simulation rate.
The joint path interpolates linearly from measured starting joints to your goal;
gripper targets apply from the first tick. Choose enough time for motion and grip
closure: 30 ticks=0.12s, 125 ticks=0.5s, 250 ticks=1s. For a stationary wait, repeat
the joint goal with a longer steps value. Physics, action execution, RGB observation,
camera updates, task counting and reward evaluation all use 250 Hz (4 ms per tick).
One task step is exactly one control tick. All budget step counts use this same
unit; remaining time is remaining_control_ticks / 250 seconds. Original episode
duration is preserved by converting the upstream step budget; general_pickup has
2000 ticks (8 seconds). Read the explicit task/elapsed simulation time in budget.
Use at most four decimal places for all numeric JSON output values; round longer
fractional values to four places. Keep steps and settle as integers. Do not round
the supplied input measurements. Local normalization enforces output precision.
Each joint target change must be <=0.15*steps radians; each executed increment is
also checked against measured state at <=0.15 rad/joint/step. Respect the supplied
joint limits with a 0.02 rad planning margin. No implicit IK or automatic recovery
is available. Tracking errors >35 mm or measured finger change >3 mm during an
established closed grip can stop the whole segment early; inspect measured changes.
ee_joint_state is a command echo, not measured opening. gripper_measured_joints
are finger positions in metres, not contact sensors. Verify retention by object
motion relative to the fingers and background in more than one view. Explain
uncertainty. Return home only when the public task requires it.
Each request has one user observation with two measured decision points.
observation.state is the current robot state; observation.memory is the latest
explicit episode memory. history.previous_observation contains the PRECEDING
DECISION POINT'S measured state, not the previous physics tick. Previous and
current images are named previous_cam_head, previous_cam_left_wrist,
previous_cam_right_wrist and current_cam_head, current_cam_left_wrist,
current_cam_right_wrist. history.duration_ms gives actual elapsed
simulation time between these decisions; the first decision has no prior view.
Compare both measured joint states and all three views to infer movement and
retention. State gripper command echoes and observation.desired_gripper_commands
are commands, not measured apertures; use gripper_measured_joints for fingers.
No previous function call, result, reasoning or action sequence is supplied.
Requested goals may have been cut short by tracking or gripper guards. Infer the
actual outcome from the current measurements; do not assume memory describes a
completed movement. Current observation fields contain joint_semantics and home
when needed. Return an updated memory for the next observation.memory.
Preserve compact episode memory. There is no frame-review, projection,
triangulation, completion-check, stop tool, arbitrary file access or hidden state.
Completion is determined by the simulator; bounded holds can inspect completion.
'''


def install():
    from egg_harness import native_responses
    from egg_harness.storage import check_configured_storage

    original = native_responses.NativeToolVision

    class VariantVision(original):
        def __init__(self, config, trace):
            super().__init__(config, trace)
            if config['tool_ablation']['mode'] != 'control_only' or config['action_interface'] != 'joint':
                raise ValueError('This variant requires control_only and the joint interface')
            if config['transport_retry'] != RETRY_POLICY:
                raise ValueError('Unexpected transport retry settings')
            self.registry.contracts = {'control_arms': self.registry.contracts['control_arms']}
            self.registry.definitions = [tool for tool in self.registry.definitions if tool['name'] == 'control_arms']
            self.client.responses.create = make_retrying_create(
                self.client.responses.create, self.trace, lambda: self.calls, check_configured_storage)
            path = self.trace / 'native-tools.json'
            record = json.loads(path.read_text())
            record.update(tools=self.registry.definitions, enabled_tools=['control_arms'],
                          history_policy='previous/current observations only; memory in observation',
                          io_variant='astra_io_250hz_unified_obspair', input_protocol='observation_pair_v1',
                          model_history_replay=False, memory_location='observation.memory', action_hz=250, observation_hz=250, task_hz=250, reward_hz=250, camera_hz=250,
                          timing_protocol='timed_joint_250hz_unified_v1', max_action_steps=2500,
                          numeric_output_decimal_places=4,
                          model_output_phase=False, external_transport_retry=RETRY_POLICY)
            path.write_text(json.dumps(record, indent=2) + '\n')

        def ask(self, question, images, system=None):
            from egg_harness.skill_policy import system_prompt
            context = json.loads(question)
            for name in ('multiview_geometry', 'temporal_geometry', 'review_capability', 'reviewed_observations'):
                context.pop(name, None)
            prefix = system_prompt('joint')
            skills = system[len(prefix):] if system and system.startswith(prefix) else (system or '')
            return super().ask(json.dumps(context, allow_nan=False), images, JOINT_SYSTEM + '\n' + skills)

    native_responses.NativeToolVision = VariantVision
    if 'egg_harness.skill_policy' in sys.modules:
        sys.modules['egg_harness.skill_policy'].Vision = VariantVision
    return VariantVision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness', type=Path, default=HARNESS)
    parser.add_argument('--mode', choices=['control_only'], default='control_only')
    args, remaining = parser.parse_known_args()
    if args.harness.resolve() != HARNESS:
        parser.error('Use this variant\'s isolated harness')
    sys.path.insert(0, str(HARNESS))
    install()
    from egg_harness import skill_policy
    sys.argv = [str(Path(__file__).resolve()), *remaining]
    skill_policy.main()


if __name__ == '__main__':
    main()
