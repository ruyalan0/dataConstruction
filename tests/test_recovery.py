
import pytest
from test_control import raw

from dojo_astra.agent import Agent
from dojo_astra.recovery import restore_agent


def test_recovery_preserves_pixels_and_rejects_unacknowledged_actions(tmp_path):
    source = tmp_path / "source"
    original = Agent(None, source / "agent", protocol="json-high")
    original.update_obs(raw())
    original.trace.event("native_action", {})
    original.update_obs(raw())
    original.trace.event("feedback", {"after": 1, "remaining_steps": 0})
    restored = Agent(None, tmp_path / "restored", protocol="json-high")
    assert restore_agent(restored, source) == 1
    assert restored.sequence == 2
    assert restored.trace.image_content(0, "head") == original.trace.image_content(0, "head")
    original.trace.event("native_action", {})
    rejected = Agent(None, tmp_path / "rejected", protocol="json-high")
    with pytest.raises(ValueError, match="unacknowledged"):
        restore_agent(rejected, source)


def test_exact_snapshot_restores_notes_and_own_count_without_result_file(tmp_path):
    import json

    from test_harness_boundary import act, client_for, three_views
    requests = []
    source = tmp_path / 'source'
    original = Agent(client_for(iter([act()]), requests), source / 'agent', protocol='json-high',
                     pin_current=True, report_steps=True)
    original.update_obs(three_views()); original.get_action(); original.update_obs(three_views())
    (source / 'episode-result.json').write_text(json.dumps({'reward': 'HIDDEN', 'success': 'HIDDEN'}))
    restored = Agent(None, tmp_path / 'restored', protocol='json-high', pin_current=True, report_steps=True)
    restore_agent(restored, source)
    assert restored.session.messages == original.session.messages
    assert restored.executed_steps == 1
    restored.update_obs(three_views())
    assert restored.executed_steps == 1
    assert 'HIDDEN' not in json.dumps(restored.session.messages)
    assert restored.trace.frame_states[0] == original.trace.frame_states[0]
