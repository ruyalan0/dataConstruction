"""Causal three-camera samples on a 4 fps simulation-time grid, kept for 5 s."""
from collections import deque
from copy import deepcopy
import json

CAMERAS = ('cam_head', 'cam_left_wrist', 'cam_right_wrist')
WINDOW_MS = 5000
SAMPLE_HZ = 4
TICK_MS = 4
WINDOW_TICKS = WINDOW_MS // TICK_MS
METADATA = dict(window_ms=WINDOW_MS, sample_hz=SAMPLE_HZ, clock='simulation',
                sampling='first_control_tick_at_or_after_250ms_grid')


def visual_history_metadata():
    return dict(rgb_memory_enabled=True, visual_history_window_ms=WINDOW_MS,
                visual_history_fps=SAMPLE_HZ)


def sample_tick(index):
    # 250 ms / 4 ms = 62.5 ticks: 0, 63, 125, 188, 250, ...
    return (125 * index + 1) // 2


def is_sample_tick(step):
    return step >= 0 and step == sample_tick(step * 2 // 125)


def expected_indices(step):
    last = step * 2 // 125
    first = max(0, (step - WINDOW_TICKS) * 2 // 125)
    return [i for i in range(first, last + 1) if step - WINDOW_TICKS <= sample_tick(i) <= step]


def validate_window(window, step):
    """Require every due sample, real timestamps and only public RGB paths."""
    if not isinstance(window, dict) or set(window) != set(METADATA) | {'frames'}:
        raise ValueError('Missing or unexpected visual_history fields')
    if any(window[key] != value for key, value in METADATA.items()):
        raise ValueError('Visual history must use 5 seconds, 4 fps and simulation time')
    frames = window['frames']
    indices = expected_indices(step)
    if not isinstance(frames, list) or len(frames) != len(indices):
        raise ValueError('Missing 4 fps samples in the current 5 second window')
    for frame, index in zip(frames, indices):
        if (not isinstance(frame, dict) or set(frame) != {'sample_index', 'step', 'target_time_ms', 'time_ms', 'cameras'}
                or type(frame['sample_index']) is not int or frame['sample_index'] != index
                or type(frame['step']) is not int or frame['step'] != sample_tick(index)
                or frame['target_time_ms'] != index * 250 or frame['time_ms'] != frame['step'] * TICK_MS
                or not isinstance(frame['cameras'], dict) or set(frame['cameras']) != set(CAMERAS)
                or any(not isinstance(p, str) or not p for p in frame['cameras'].values())):
            raise ValueError('Invalid or noncausal 4 fps sample')
    return deepcopy(frames)


class RollingVisualHistory:
    """One fresh episode, bounded memory; the optional index is durable audit data."""
    def __init__(self, audit_path=None):
        self.frames = deque()
        self.next_index = 0
        self.last_step = -1
        self.audit_path = audit_path

    def observe(self, step, cameras):
        if type(step) is not int or step < self.last_step or step < 0:
            raise ValueError('Visual history cannot rewind simulation time')
        due = sample_tick(self.next_index)
        if step > due:
            raise ValueError(f'Missed required 4 fps capture at control tick {due}')
        if step == due:
            if set(cameras) != set(CAMERAS):
                raise ValueError('A 4 fps sample requires all three cameras')
            frame = dict(sample_index=self.next_index, step=step, target_time_ms=self.next_index * 250,
                         time_ms=step * TICK_MS, cameras={name: str(cameras[name]) for name in CAMERAS})
            self.frames.append(frame)
            self.next_index += 1
            if self.audit_path is not None:
                with self.audit_path.open('a') as stream:
                    stream.write(json.dumps(frame, allow_nan=False) + '\n')
        self.last_step = step
        while self.frames and self.frames[0]['step'] < step - WINDOW_TICKS:
            self.frames.popleft()
        result = dict(METADATA, frames=deepcopy(list(self.frames)))
        validate_window(result, step)
        return result
