"""Fresh task splits reject exposed layouts without changing historical defaults."""
from copy import deepcopy
from egg_harness.layout_split import heldout_layouts
base = {"development_layouts": [20,21,22], "heldout_layouts": [23,24,25]}
assert heldout_layouts(base,"align_blocks") == [23,24,25]
updated = {**base,"heldout_layouts_by_task":{"solve_equation":[17,18,19]},"development_exposed_layouts_by_task":{"solve_equation":[23]}}
assert heldout_layouts(updated,"solve_equation") == [17,18,19]
assert heldout_layouts(updated,"align_blocks") == [23,24,25]
for values in ([23,24,25],[17,20,19],[17,17,19],[17,18],[17,18,True],[-1,18,19]):
    bad=deepcopy(updated);bad["heldout_layouts_by_task"]["solve_equation"]=values
    try: heldout_layouts(bad,"solve_equation")
    except ValueError: pass
    else: raise AssertionError(values)
print("Task-specific fresh splits and exposed-case rejection passed")
