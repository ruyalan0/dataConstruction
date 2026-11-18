"""Check actual per-tick evidence for completed unified 250 Hz episodes."""
import argparse
from datetime import datetime, timezone
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'harness'))
from egg_harness.control_timing import validate_timing_record


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(run):
    result = read(run / 'results.json')[0]
    seed = result['seed_index']
    episode = run / f'seed-{seed}'
    native = result['official_observation']
    ticks = native['steps']
    require(result['status'] == 'native_end' and native['native_terminal'] and native['valid_sample']
            and not native['unstable'], 'Episode did not reach a valid native terminal state')
    require(result['release_unchanged'] and result['policy_returncode'] == result['simulator_returncode'] == 0,
            'Runtime or source integrity check failed')
    validate_timing_record(read(episode / 'control-timing.json'))
    launch = read(run / 'launch-record.json')
    for field in ('action_hz', 'observation_hz', 'camera_hz', 'task_hz', 'reward_hz'):
        require(launch[field] == 250, 'Wrong launch frequency: ' + field)
    runtime = read(episode / 'unified-runtime.json')
    require(runtime['observation_hz'] == 250 and runtime['physics_ticks_per_observation'] == 1
            and runtime['physics_step_seconds'] == .004 and runtime['render_interval'] == 1,
            'Actual simulator timing differs from unified 250 Hz')
    require(runtime['task_step_limit'] == runtime['native_task_step_limit'] == 2000
            and runtime['source_task_step_limit'] == 200 and runtime['source_task_horizon_seconds'] == 8.,
            'general_pickup budget conversion changed the eight-second duration')
    require(native['task_steps'] == native['reward_updates'] == ticks,
            'Task count or reward updates do not cover every control tick')
    require(native['elapsed_simulation_seconds'] == ticks / 250 and native['task_horizon_seconds'] == 8.,
            'Reported simulation time mismatch')
    actions = [json.loads(line) for line in (episode / 'actions.jsonl').read_text().splitlines()]
    require([action['step'] for action in actions] == list(range(1, ticks + 1)),
            'Action log is not continuous at every 4 ms tick')
    frame_paths = list((episode / 'frames').glob('step-*.json'))
    require(len(frame_paths) == ticks, 'RGB observation count differs from control tick count')
    for step in range(1, ticks + 1):
        tag = f'step-{step:04d}'
        frame = read(episode / 'frames' / f'{tag}.json')
        budget = frame['budget']
        require(frame['step'] == budget['executed_native_steps'] == budget['executed_control_ticks'] == step,
                f'Observation/task/control counts disagree at tick {step}')
        require(budget['total_native_steps'] == budget['total_control_ticks'] == 2000
                and budget['remaining_native_steps'] == budget['remaining_control_ticks'] == 2000-step,
                f'Budget units disagree at tick {step}')
        for camera in ('cam_head', 'cam_left_wrist', 'cam_right_wrist'):
            image = episode / 'frames' / f'{tag}-{camera}.jpg'
            require(image.is_file() and image.stat().st_size > 0, f'Missing RGB image at {step}: {camera}')
    video = run / 'rendered/rollout.mp4'
    media = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=r_frame_rate,nb_frames,duration', '-of', 'json', str(video)], text=True))['streams'][0]
    require(Fraction(media['r_frame_rate']) == 250 and int(media['nb_frames']) == ticks+1,
            'Playback does not preserve the per-tick RGB sequence at 250 FPS')
    report = dict(passed=True, validated_at=datetime.now(timezone.utc).isoformat(), run=str(run), layout=seed,
        success=result['success'], score=native['score'], control_ticks=ticks, task_ticks=native['task_steps'],
        reward_updates=native['reward_updates'], rgb_observations=len(frame_paths), rgb_images=3*len(frame_paths),
        task_horizon_seconds=8., simulation_seconds=ticks/250, video_fps=250, video_frames=ticks+1,
        protocol=launch['timing_protocol'], all_live_frequencies_hz=250, originals_unchanged=result['release_unchanged'])
    destination = run / 'unified-validation.json'
    if destination.exists():
        previous = read(destination)
        if all(previous.get(key) == value for key, value in report.items() if key != 'validated_at'):
            return previous
    destination.write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, action='append', required=True)
    args = parser.parse_args()
    print(json.dumps([validate(run.resolve()) for run in args.run], indent=2))
