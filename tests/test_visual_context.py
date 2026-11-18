from copy import deepcopy

import numpy as np

from dojo_astra.arx_x5_interpreter import rotation
from dojo_astra.visual_context import trim_images


def test_window_preserves_current_images_text_and_tool_results():
    messages = []
    for frame in range(4):
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"frame {frame}"},
                    *[{"type": "image_url", "image_url": {"url": f"{frame}-{c}"}} for c in range(3)],
                ],
            }
        )
        messages.append({"role": "assistant", "content": "reasoning", "tool_calls": [{"id": str(frame)}]})
        messages.append({"role": "tool", "tool_call_id": str(frame), "content": "feedback"})
    original = deepcopy(messages)
    stats = trim_images(messages, 6)
    assert stats == {"images_sent": 6, "images_evicted": 6}
    assert messages[-6:] == original[-6:]
    assert messages[1:3] == original[1:3]
    assert messages[0]["content"][0]["text"] == "frame 0"
    assert all(p["type"] == "text" for p in messages[0]["content"])
    messages.append({"role": "user", "content": [original[0]["content"][1]]})
    trim_images(messages, 6)
    assert messages[-1]["content"][0]["image_url"]["url"] == "0-0"


def test_link_basis_uses_wxyz_and_columns_for_tool_translation():
    pose = np.array([0, 0, 0, 2**-0.5, 0, 0, 2**-0.5])
    basis = rotation(pose).as_matrix()
    np.testing.assert_allclose(basis[:, 0], [0, 1, 0], atol=1e-12)
