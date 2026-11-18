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
the joint goal with a longer steps value. Task budgets retain the original 25 Hz
clock: one task step contains ten control ticks. Read step_unit and the explicit
task/elapsed simulation time in budget. Do not interpret task steps as 4 ms ticks.
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
Each request has one user message with a chronological visual_history: three
camera images sampled at 4 fps over the last 5 seconds of SIMULATION time, plus
the exact previous decision observation and the current observation. Read each
entry's step, relative_time_ms, roles and ordered image names. The 250 ms sample
grid is rounded up to the next 4 ms control tick (at most 2 ms later). Historical
samples contain RGB only; joint state is supplied at the two decision endpoints.
Fewer samples exist at episode start. A previous decision older than 5 seconds
is still included as an explicit endpoint. Entries sharing a sampled time and
the previous decision are merged; a rejected zero-motion action may leave two
decision endpoints at the same time. Do not infer movement merely from ordering.
observation.state is the current robot state; observation.memory is the latest
explicit episode memory. history.previous_observation contains the PRECEDING
DECISION POINT'S measured state, not the previous physics tick. Images are named
observation_NN_cam_head, observation_NN_cam_left_wrist and
observation_NN_cam_right_wrist, in visual_history.observations order.
history.duration_ms gives actual elapsed
simulation time between these decisions; the first decision has no prior view.
Compare the visual sequence, both measured joint states and all three views to infer movement and
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
                          history_policy='5s RGB at 4fps plus previous/current decision observations; memory in observation',
                          visual_history_window_ms=5000, visual_history_fps=4,
                          io_variant='astra_io_250hz_obspair_4fps', input_protocol='visual_history_5s_4fps_v1',
                          model_history_replay=False, memory_location='observation.memory', action_hz=250, observation_hz=25,
                          timing_protocol='timed_joint_v1', max_action_steps=2500,
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
