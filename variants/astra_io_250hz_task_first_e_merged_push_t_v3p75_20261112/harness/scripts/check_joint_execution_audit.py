"""Generate real IPC records and reject deliberate recorded-evidence corruption."""
import ast
import copy
import json
import tempfile
from pathlib import Path
from audit_joint_execution import audit
from audit_contraction_execution import audit as mixed_audit

# Reuse fixture definitions without running the module's standalone test suite.
source = Path('scripts/check_joint_execution.py')
tree = ast.parse(source.read_text())
tree.body = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef, ast.Assign))]
fixtures = {'__name__': 'joint_audit_fixtures'}
exec(compile(tree, str(source), 'exec'), fixtures)
Env, Session, execute = (fixtures[name] for name in ('Env', 'Session', 'execute'))

def edit_json(path, mutate):
    previous = path.read_text(); value = json.loads(previous); mutate(value); path.write_text(json.dumps(value))
    return previous

def reject(path, mutate, episode, through):
    previous = edit_json(path, mutate)
    try:
        try: audit(episode, through)
        except (ValueError, KeyError, TypeError): pass
        else: raise AssertionError('Tampered evidence accepted: ' + str(path))
    finally: path.write_text(previous)

for fault in (None, 'held_drift'):
    with tempfile.TemporaryDirectory() as directory:
        episode = Path(directory); env = Env(fault); session = Session(episode, env)
        result = execute(session); through = result['step']; identifier = session.robot.last_command_id
        checked = audit(episode, through)
        assert checked['commands'][identifier]['executed_steps'] == through
        assert mixed_audit(episode, through)['joint_control']['joint_steps'] == list(range(1, through + 1))
        response = episode / 'responses' / (identifier + '.json')
        reject(response, lambda data: data['joint_control'].__setitem__('next_motion_verified', True), episode, through)
        reject(episode / (identifier + '-joint-plan.json'), lambda data: data['state']['left_arm_joint_state'].__setitem__(0, .4), episode, through)
        reject(episode / 'commands' / (identifier + '.json'), lambda data: data.__setitem__('steps', 1), episode, through)
        action_file = episode / 'actions.jsonl'; original = action_file.read_text(); rows = [json.loads(line) for line in original.splitlines()]
        for corruption in ('held_joint', 'grip', 'fk', 'bound', 'step_limit'):
            altered = copy.deepcopy(rows)
            if corruption == 'held_joint': altered[0]['action']['left_arm_joint_state'][5] += .001
            elif corruption == 'grip': altered[0]['action']['right_ee_joint_state'] = [0.]
            elif corruption == 'fk': altered[0]['cartesian_target']['left_ee_pose'][0] += .001
            elif corruption == 'bound': altered[0]['joint_bounds']['arms']['left']['per_point_swept_bound_m'][6] = 0
            else: altered[0]['action']['left_arm_joint_state'][1] += .2
            action_file.write_text(''.join(json.dumps(row) + chr(10) for row in altered))
            try: audit(episode, through)
            except ValueError: pass
            else: raise AssertionError('Accepted ' + corruption)
            action_file.write_text(original)
        if fault is None and through > 1:
            first_frame = episode / 'frames' / 'step-0001.json'
            reject(first_frame, lambda data: data['state']['right_arm_joint_state'].__setitem__(0, data['state']['right_arm_joint_state'][0] + .05), episode, through)
        session.finish()

with tempfile.TemporaryDirectory() as directory:
    episode = Path(directory); env = Env(); session = Session(episode, env)
    # Create a genuine mixed Cartesian-contraction + joint-control prefix.
    session.robot.command('move_local', arms={'left': {'position': env.get_obs()['state']['left_ee_pose'][:3]}}, steps=1, contract_local_ik=True)
    result = execute(session)
    assert mixed_audit(episode, result['step'])['joint_control']['joint_steps'][0] == 2
    session.finish()

with tempfile.TemporaryDirectory() as directory:
    episode = Path(directory); env = Env(); session = Session(episode, env)
    geometry = fixtures['geometry'](fixtures['MODEL'], env.get_obs()['state'], 'left')
    np = fixtures['np']; points = np.asarray(geometry['origins_world_m'] + [geometry['finger_proxy_world_m']])
    spec = {'arm': 'left', 'reason': 'descent_blocked', 'max_steps': 24, 'clearance_box': [(points.min(axis=0) - .18).tolist(), (points.max(axis=0) + .18).tolist()]}
    preview = session.robot.command('preview_unfold_arm', unfold_arm=spec)
    result = session.robot.command('unfold_arm', unfold_arm=spec, preview_id=preview['joint_preview']['preview_id'])
    report = audit(episode, result['step']); assert report['commands'][session.robot.last_command_id]['primitive'] == 'unfold_arm'
    session.finish()

print('Joint recorded audit passed: independent bounds/FK, preview binding, fixed holds/grips, mixed dispatch, unfold, bad-tracking stop and evidence-tamper rejection; mock IPC only')
