"""Completed component probes cannot become task success or frozen results."""
import json
import tempfile
from pathlib import Path
from egg_harness.runner import completed_bilateral_probe

with tempfile.TemporaryDirectory() as directory:
    trace = Path(directory)
    path = trace / 'policy-result.json'
    path.write_text(json.dumps({'status': 'probe_completed', 'not_task_success': True}))
    assert completed_bilateral_probe({'probe': 'bilateral'}, 'development', trace)
    assert not completed_bilateral_probe({}, 'development', trace)
    try:
        completed_bilateral_probe({'probe': 'bilateral'}, 'frozen', trace)
    except ValueError:
        pass
    else:
        raise AssertionError('Frozen probe accepted')
    path.write_text(json.dumps({'status': 'probe_failed', 'not_task_success': True}))
    try:
        completed_bilateral_probe({'probe': 'bilateral'}, 'development', trace)
    except ValueError:
        pass
    else:
        raise AssertionError('Failed probe accepted')
print('Probe completion stays separate from native task acceptance')
