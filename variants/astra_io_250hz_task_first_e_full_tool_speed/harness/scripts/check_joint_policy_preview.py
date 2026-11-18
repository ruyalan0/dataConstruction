"""Policy preflight rejects copied/stale tokens before constructing movement IPC."""
import ast
import copy
from pathlib import Path
from egg_harness.skill_policy import bind_joint_preview

sent = []
def dispatch(pending, step, primitive, options, token):
    bound = bind_joint_preview(pending, step, primitive, options, token)
    sent.append({'op': primitive, **bound})

token = 'a' * 64
options = {'joint_delta': {'left': {'joint2': -.1, 'joint3': .08}}, 'steps': 3}
pending = {'step': 619, 'primitive': 'joint_delta', 'options': copy.deepcopy(options), 'preview_id': token}

invalid = [
    (None, 619, 'joint_delta', options, token),
    (pending, 619, 'joint_delta', options, 'b' * 64),
    (pending, 620, 'joint_delta', options, token),
    (pending, 619, 'unfold_arm', options, token),
    (pending, 619, 'joint_delta', {**options, 'steps': 4}, token),
    (pending, 619, 'joint_delta', {'joint_delta': {'left': {'joint2': -.1, 'joint3': -.08}}, 'steps': 3}, token),
    (pending, 619, 'joint_delta', options, True),
    (pending, 619, 'joint_delta', options, 'g' * 64),
    (pending, 619, 'joint_delta', options, 'a' * 63),
]
for args in invalid:
    before = len(sent)
    try: dispatch(*args)
    except ValueError: pass
    else: raise AssertionError('Invalid latest-preview binding reached IPC')
    assert len(sent) == before

saved = copy.deepcopy(pending)
dispatch(pending, 619, 'joint_delta', options, token)
assert sent[-1] == {'op': 'joint_delta', **options, 'preview_id': token}
assert pending == saved and 'preview_id' not in options, 'Binding mutated the retained preview specification'

# A newer zero-step preview invalidates the old token even without native motion.
newest = {**pending, 'preview_id': 'c' * 64}
try: dispatch(newest, 619, 'joint_delta', options, token)
except ValueError: pass
else: raise AssertionError('Old token remained executable after a newer preview')
dispatch(newest, 619, 'joint_delta', options, newest['preview_id'])

unfold = {'unfold_arm': {'arm': 'right', 'reason': 'descent_blocked', 'clearance_box': [[-.4, -.4, .1], [.4, .4, .9]], 'max_steps': 24}}
pending_unfold = {'step': 700, 'primitive': 'unfold_arm', 'options': copy.deepcopy(unfold), 'preview_id': 'd' * 64}
changed = copy.deepcopy(unfold); changed['unfold_arm']['clearance_box'][0][0] = -.3
try: dispatch(pending_unfold, 700, 'unfold_arm', changed, 'd' * 64)
except ValueError: pass
else: raise AssertionError('Changed unfolded clearance box reached IPC')
dispatch(pending_unfold, 700, 'unfold_arm', unfold, 'd' * 64)

# Integration assertion: the real policy uses this guard in its pre-IPC try.
tree = ast.parse(Path('egg_harness/skill_policy.py').read_text())
calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'bind_joint_preview']
assert len(calls) == 1
guard = calls[0]
joint_ipc = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'command' and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == 'kind']
assert len(joint_ipc) == 1 and guard.lineno < joint_ipc[0].lineno
assert any(isinstance(node, ast.Try) and any(item is guard for item in ast.walk(node)) and node.handlers for node in ast.walk(tree)), 'Binding failure must remain pre-IPC model feedback'
print('Policy latest-preview preflight passed: wrong/copied/stale token, step, primitive and nested spec refused before IPC; unchanged delta/unfold accepted without mutating pending state')
