import importlib.util
from pathlib import Path

from test_harness_boundary import act, client_for, three_views

from dojo_astra.agent import Agent


def test_metrics_separate_rejected_tools_from_native_actions(tmp_path):
    spec = importlib.util.spec_from_file_location('summary', Path('scripts/summarize_episode.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bad = {'name': 'recall', 'arguments': {'sequence': 999, 'camera': 'cam_head'}}
    agent = Agent(client_for(iter([bad, act(8)]), []), tmp_path / 'agent', protocol='json-high',
                  compact_evidence=True, pin_current=True)
    agent.update_obs(three_views())
    for _ in range(3):
        agent.get_action()
        agent.update_obs(three_views())
    summary = module.summarize(tmp_path)
    assert summary['api_attempts'] == 2
    assert summary['operation_counts'] == {'recall': 1, 'act': 1}
    assert summary['native_actions'] == summary['native_feedback_boundaries'] == 3
    assert summary['validation_errors_repaired'] == 1
    assert summary['validation_errors_unresolved'] == 0
    assert summary['low_progress_interruptions'] == 1
    assert summary['low_progress_boundary_ratio'] == 1
    assert summary['sustained_low_progress_boundary_ratio'] == 1 / 3
    assert summary['max_active_text_bytes'] <= 65536
    assert summary['distinct_images_per_request'] == [3, 3]
