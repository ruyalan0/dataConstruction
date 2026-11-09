"""Read only commanded Cartesian targets, never evaluator or scene state."""
import json
from pathlib import Path
import numpy as np


def executed_motion(episode, command_id, start_step, end_step):
    if end_step < start_step:
        raise ValueError('Invalid motion interval')
    episode=Path(episode)
    records=[]
    if end_step > start_step:
        with (episode/'actions.jsonl').open() as stream:
            for line in stream:
                row=json.loads(line)
                if start_step < row['step'] <= end_step:
                    if row['command'] != command_id:
                        raise ValueError('Executed motion belongs to another controller')
                    records.append(row)
        if [r['step'] for r in records] != list(range(start_step+1,end_step+1)):
            raise ValueError('Missing or duplicate executed motion evidence')
    attempts=[]
    path=episode/'ik-contractions.jsonl'
    if path.exists():
        with path.open() as stream:
            attempts=[json.loads(line) for line in stream]
        attempts=[r for r in attempts if r['command']==command_id]
    return {'targets':records[-1]['cartesian_target'] if records else {},
            'contraction_attempts':attempts,
            'contracted':any(r['scale'] is not None and r['scale']<1 for r in attempts)}


def target_for_arm(evidence, arm, before_pose, elapsed):
    pose=np.asarray(evidence['targets'][arm+'_ee_pose'] if elapsed else before_pose,dtype=float)
    if pose.shape != (7,) or not np.isfinite(pose).all():
        raise ValueError('Invalid executed target evidence')
    return pose[:3],pose[3:]
