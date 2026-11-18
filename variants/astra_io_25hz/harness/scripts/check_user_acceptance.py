"""Explicit task acceptance remains separate from native benchmark results."""
from audit_task_order import user_acceptances, ordering_rows
entry={'task':'pickup','accepted_at':'2026-09-15T10:00:00+00:00','authority':'explicit_user_instruction','instruction':'Accept pickup','automatic_benchmark_pass':False}
tasks=[{'task':'pickup'},{'task':'next'}]
accepted=user_acceptances({'user_acceptances':[entry]},tasks)
assert accepted['pickup']['acceptance_basis']=='explicit_user_instruction'
assert 'report' not in accepted['pickup']
assert not ordering_rows(tasks,accepted,{'next':accepted['pickup']['completed_at_unix']+1},[])[1]['preceding_task_violations']
assert ordering_rows(tasks,accepted,{'next':accepted['pickup']['completed_at_unix']-1},[])[1]['preceding_task_violations']==['pickup']
for change in ({'authority':'inferred'},{'automatic_benchmark_pass':True},{'instruction':''},{'task':'unknown'},{'accepted_at':'2026-09-15T10:00:00'}):
    try:user_acceptances({'user_acceptances':[{**entry,**change}]},tasks)
    except ValueError:pass
    else:raise AssertionError(change)
try:user_acceptances({'user_acceptances':[entry,entry]},tasks)
except ValueError:pass
else:raise AssertionError('Duplicate acceptance accepted')
print('User acceptance: explicit authority, separate score basis, timestamps and task identity pass')
