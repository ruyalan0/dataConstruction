"""Recorded native replay before ready, preserving budget, grip intent and provenance."""
import ast
import json
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from native_replay import load_replay
from motion_server_fixture import run_server

fixture_source = Path('scripts/check_joint_execution.py')
tree = ast.parse(fixture_source.read_text()); tree.body = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef, ast.Assign))]
fixtures = {'__name__': 'replay_fixture'}; exec(compile(tree, str(fixture_source), 'exec'), fixtures)
Env, Session, execute = (fixtures[name] for name in ('Env', 'Session', 'execute'))

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory); original = root / 'source'; original.mkdir()
    first = Env(); session = Session(original, first); endpoint = execute(session)['step']; session.finish()
    plan = load_replay(original, endpoint, 'classify_objects', 20, 'development')
    for changes in ((endpoint, 'other_task', 20, 'development'), (endpoint, 'classify_objects', 21, 'development'), (endpoint, 'classify_objects', 20, 'frozen'), (endpoint + 1, 'classify_objects', 20, 'development')):
        try: load_replay(original, *changes)
        except ValueError: pass
        else: raise AssertionError('Invalid replay source accepted')
    destination = root / 'replay'; destination.mkdir(); second = Env()
    second.scene_manager = SimpleNamespace(layout_manager=SimpleNamespace(get_labels_by_prefix=lambda *args: []))
    ready_during_action = []; actual_take_action = second.take_action
    def inspect_action(action):
        ready_during_action.append((destination / 'ready.json').exists()); actual_take_action(action)
    second.take_action = inspect_action; errors = []
    def run():
        try: run_server(second, destination, [20], 'development', 'classify_objects', replay=plan)
        except BaseException as error: errors.append(error)
    thread = threading.Thread(target=run, daemon=True); thread.start(); deadline = time.monotonic() + 15
    while not (destination / 'ready.json').exists():
        if errors: raise errors[0]
        if time.monotonic() > deadline: raise TimeoutError('Replay did not expose ready')
        time.sleep(.01)
    assert not any(ready_during_action) and second.take_action_cnt == [endpoint]
    assert len(second.actions) == len(first.actions)
    assert all(np.array_equal(a[key], b[key]) for a, b in zip(first.actions, second.actions) for key in a)
    latest = json.loads((destination / 'latest.json').read_text())
    assert latest['budget']['remaining_native_steps'] == second.step_lim - endpoint
    assert not {'replay', 'objects', 'replay_comparison'} & set(latest)
    report = json.loads((destination / 'developer-only/replay-comparison.json').read_text())
    assert report['exact_physics_restoration'] is False
    assert report['objects']['status'] == 'missing_same_step_source_diagnostic'
    assert all(value < 1e-8 for errors in report['maximum_prefix_errors'].values() for value in errors.values())
    assert len((destination / 'developer-only/replay-prefix-errors.jsonl').read_text().splitlines()) == endpoint
    assert len(list((destination / 'frames').glob('step-*.json'))) == endpoint
    assert not list((destination / 'commands').glob('*.json')), 'Original high-level commands became pending actions'
    trace = root / 'trace'; trace.mkdir(); robot = fixtures['Robot'](destination, trace)
    assert robot.obs['step'] == endpoint
    robot.command('finish'); thread.join(3); assert not thread.is_alive() and not errors
print('Native replay passed: source/task/seed/prefix validation, exact recorded action calls before ready, original budget, per-step public evidence, developer-only errors and no duplicate high-level execution')
