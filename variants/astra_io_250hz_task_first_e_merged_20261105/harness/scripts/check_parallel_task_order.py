"""Parallel authorization removes only intra-group ordering dependencies."""
from audit_task_order import ordering_rows

tasks = [{'task': name} for name in ['align', 'language', 'equation', 'pickup', 'next']]
accepted = {'align': {'completed_at_unix': 10}}
starts = {'align': 0, 'language': 20, 'equation': 21, 'pickup': 22, 'next': 23}
serial = ordering_rows(tasks, accepted, starts, [])
assert serial[2]['preceding_task_violations'] == ['language']
parallel = ordering_rows(tasks, accepted, starts, [['language', 'equation', 'pickup']])
assert all(not row['preceding_task_violations'] for row in parallel[:4])
assert parallel[4]['preceding_task_violations'] == ['language', 'equation', 'pickup']
assert all(not row['accepted'] for row in parallel[1:])
assert ordering_rows(tasks, {}, starts, [['language', 'equation', 'pickup']])[2]['preceding_task_violations'] == ['align']
assert ordering_rows(tasks, {'align': {'completed_at_unix': 30}}, starts, [['language', 'equation', 'pickup']])[2]['preceding_task_violations'] == ['align']
for invalid in [[['language']], [['language', 'unknown']], [['language', 'language']], [['language', 'pickup'], ['pickup', 'equation']]]:
    try:
        ordering_rows(tasks, accepted, starts, invalid)
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid parallel authorization accepted')
print('Parallel ordering preserves prerequisites, later task gates, timestamps and independent acceptance')

rolling_tasks=[{'task':name} for name in ['align','a','b','c','next']]
rolling_starts={'align':0,'a':20,'b':21,'c':22,'next':40}
rolling_accepted={'align':{'completed_at_unix':10},'b':{'completed_at_unix':30}}
rolling=ordering_rows(rolling_tasks,rolling_accepted,rolling_starts,[],3)
assert not rolling[-1]['preceding_task_violations']
assert ordering_rows(rolling_tasks,{'align':{'completed_at_unix':10}},rolling_starts,[],3)[-1]['preceding_task_violations']==['a','b','c']
assert ordering_rows(rolling_tasks,rolling_accepted,{**rolling_starts,'b':50},[],3)[-1]['preceding_task_violations']==['b']
assert not rolling[-1]['accepted']
print('Rolling parallelism fills accepted task slots without skipping unstarted tasks or exceeding capacity')
