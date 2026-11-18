"""Persistent, sequential policy subprocess with bounded JSON-line transport."""

import base64
import io
import json
import os
import selectors
import subprocess

import numpy as np
from PIL import Image

from .observation_adapter import adapt


def encode_observation(raw):
    obs = adapt(raw, 0)
    state, vision = {}, {}
    for arm, pose in obs.poses.items():
        state[f"{arm}_ee_pose"] = pose.tolist()
        state[f"{arm}_ee_joint_state"] = [obs.gripper_commands[arm]]
    for camera, pixels in obs.images.items():
        output = io.BytesIO()
        Image.fromarray(pixels).save(output, format="PNG")
        vision[camera] = {"color": base64.b64encode(output.getvalue()).decode()}
    return {"data_format_version": "v1.0", "instruction": obs.instruction, "state": state, "vision": vision}


def decode_observation(raw):
    for camera in raw["vision"].values():
        camera["color"] = np.asarray(Image.open(io.BytesIO(base64.b64decode(camera["color"]))).convert("RGB"))
    return raw


class ProcessPolicy:
    def __init__(self, command, log_path, timeout=180):
        with open(log_path, "xb") as log:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log
            )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.timeout, self.sequence, self.buffer = timeout, 0, b""

    def call(self, operation, **payload):
        request = {"id": self.sequence, "operation": operation, **payload}
        self.process.stdin.write(json.dumps(request, allow_nan=False).encode() + b"\n")
        self.process.stdin.flush()
        import time

        deadline = None if self.timeout is None else time.monotonic() + self.timeout
        while b"\n" not in self.buffer:
            if not self.selector.select(None if deadline is None else max(0, deadline - time.monotonic())):
                raise TimeoutError("Policy response timeout")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("Policy process closed its response stream")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        response = json.loads(line)
        if response["id"] != self.sequence:
            raise RuntimeError("Policy response sequence mismatch")
        self.sequence += 1
        if not response["ok"]:
            raise RuntimeError(response["error"])
        return response["result"]

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.selector.close()
        self.process.stdin.close()
        self.process.stdout.close()
