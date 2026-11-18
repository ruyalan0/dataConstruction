"""Isolate camera rendering in CPU motion/IPC tests of the real server loop.

Camera certification and render synchronization have their own multiview tests
and GPU qualification; the motion fixtures intentionally supply tiny fake RGB.
"""
from unittest.mock import patch

import server_loop


def run_server(*args, **kwargs):
    # Historical motion regressions intentionally exercise every legacy operation.
    kwargs.setdefault("action_interface", "both")
    with patch.object(server_loop, 'capture_public_observation', side_effect=lambda env: env.get_obs()), \
         patch.object(server_loop, 'validate_camera_rig', return_value='a' * 64):
        return server_loop.run_server(*args, **kwargs)
