"""Restore only the previous policy's public context at an unchanged scene step."""
import hashlib
import json
from pathlib import Path


def load_context(request_path, episode, step):
    path=Path(request_path).resolve(); episode=Path(episode).resolve()
    if not path.is_relative_to(episode.parent) or path.parent.name!='model' or not path.name.endswith('.request.json'):
        raise ValueError('Continuation request must belong to this episode run')
    raw=path.read_bytes(); request=json.loads(raw); context=json.loads(request['question'])
    if type(context.get('step')) is not int or context['step']!=step:
        raise ValueError('Continuation context step differs from current scene')
    if not isinstance(context.get('memory'),str):raise ValueError('Missing policy memory')
    if not isinstance(context.get('recent_executed_segments'),list) or not isinstance(context.get('stalled_segments_this_controller'),list):
        raise ValueError('Invalid public motion history')
    previous=None
    observed=context.get('previous_observation')
    if observed is not None:
        prior_step=observed['step']
        if type(prior_step) is not int or not 0<=prior_step<=step:raise ValueError('Invalid prior observation step')
        name='initial' if prior_step==0 else f'step-{prior_step:04d}'
        frame=json.loads((episode/'frames'/(name+'.json')).read_text())
        if frame['step']!=prior_step or frame['state']!=observed['state']:raise ValueError('Prior public observation mismatch')
        cameras={}
        for key in ('cam_head','cam_left_wrist','cam_right_wrist'):
            image=Path(frame['cameras'][key]).resolve()
            if not image.is_relative_to(episode/'frames') or not image.is_file():raise ValueError('Prior image unavailable in episode')
            cameras[key]=str(image)
        arm=observed['active_arm']
        arms=arm if isinstance(arm,list) else ([] if arm is None else [arm])
        if any(item not in ('left','right') for item in arms):raise ValueError('Invalid prior active arm')
        previous={'step':prior_step,'state':frame['state'],'cameras':cameras,'arm':arm}
    return {'memory':context['memory'],'feedback':context.get('previous_result'),'previous':previous,
            'recent':context['recent_executed_segments'][-8:],
            'stalls':context['stalled_segments_this_controller'][-12:],
            'source_request':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'step':step}
