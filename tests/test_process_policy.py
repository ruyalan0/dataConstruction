import sys

import numpy as np
import pytest
from test_control import raw

from dojo_astra.process_policy import ProcessPolicy, decode_observation, encode_observation


def test_transport_only_serializes_allowed_observation():
    original = raw()
    encoded = encode_observation(original)
    assert set(encoded) == {"data_format_version", "instruction", "state", "vision"}
    assert set(encoded["vision"]["head"]) == {"color"}
    decoded = decode_observation(encoded)
    np.testing.assert_array_equal(decoded["vision"]["head"]["color"], original["vision"]["head"]["color"])


def test_persistent_process_and_request_order(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(
        "import sys,json; [print(json.dumps(dict(id=i,ok=True,result=i)),flush=True) for i,line in enumerate(sys.stdin)]"
    )
    policy = ProcessPolicy([sys.executable, "-u", str(script)], tmp_path / "log", timeout=2)
    try:
        assert policy.call("first") == 0
        assert policy.call("second") == 1
    finally:
        policy.close()
    assert policy.process.poll() is not None


def test_process_failure_is_not_retried(tmp_path):
    policy = ProcessPolicy([sys.executable, "-c", "raise SystemExit(1)"], tmp_path / "log", timeout=2)
    try:
        with pytest.raises((RuntimeError, BrokenPipeError)):
            policy.call("act")
        assert policy.sequence == 0
    finally:
        policy.close()
