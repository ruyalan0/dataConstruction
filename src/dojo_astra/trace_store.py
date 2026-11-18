import base64
import hashlib
import io
import json
import os
from pathlib import Path

from PIL import Image


class TraceStore:
    def __init__(self, root, episode):
        self.root = Path(root) / episode
        self.root.mkdir(parents=True, exist_ok=False)
        self.frames = {}
        self.frame_states = {}
        (self.root / 'blobs').mkdir()
        (self.root / 'objects').mkdir()

    def event(self, kind, data):
        with (self.root / "events.jsonl").open("a") as out:
            out.write(json.dumps({"kind": kind, "data": data}, allow_nan=False) + "\n")
            out.flush()
            os.fsync(out.fileno())

    def observation(self, obs):
        cameras = {}
        for i, (name, image) in enumerate(obs.images.items()):
            path = self.root / f"frame_{obs.sequence:06d}_cam_{i}.png"
            buffer = io.BytesIO()
            Image.fromarray(image).save(buffer, format='PNG')
            digest = self._store('blobs', buffer.getvalue())
            os.link(self.root / 'blobs' / digest, path)
            cameras[name] = path
        self.frames[obs.sequence] = cameras
        self.frame_states[obs.sequence] = {
            "poses": {a: p.tolist() for a, p in obs.poses.items()},
            "gripper_command_echo": obs.gripper_commands,
            "observation_sequence": obs.sequence,
        }
        self.event(
            "observation",
            {
                "sequence": obs.sequence,
                "instruction": obs.instruction,
                "poses": {a: p.tolist() for a, p in obs.poses.items()},
                "gripper_command_echo": obs.gripper_commands,
                "images": {c: p.name for c, p in cameras.items()},
            },
        )

    def image_content(self, sequence, camera):
        path = self.frames[sequence][camera]
        data = base64.b64encode(path.read_bytes()).decode()
        return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}

    def save_session(self, messages, calls, pending):
        temporary = self.root / "session.tmp"
        temporary.write_text(json.dumps({'format': 'dojo-session-v2', 'messages': self.message_refs(messages),
                                         'calls': calls, 'pending': pending}))
        temporary.replace(self.root / "session.json")

    def save_context_archive(self, messages, calls):
        with (self.root / "context-archive.jsonl").open("a") as out:
            out.write(json.dumps({'format': 'dojo-session-v2', 'calls': calls,
                                  'messages': self.message_refs(messages)}) + chr(10))
            out.flush()
            os.fsync(out.fileno())

    def _store(self, directory, data):
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / directory / digest
        if not path.exists():
            with path.open('xb') as out:
                out.write(data)
        return digest

    def _pack(self, value):
        if isinstance(value, str) and value.startswith('data:image/'):
            header, encoded = value.split(',', 1)
            data = base64.b64decode(encoded, validate=True)
            return {'$image_blob': self._store('blobs', data), 'header': header}
        if isinstance(value, dict):
            return {k: self._pack(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._pack(v) for v in value]
        return value

    def message_refs(self, messages):
        return [self._store('objects', json.dumps(self._pack(m), sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode()) for m in messages]

    @staticmethod
    def load_messages(root, refs):
        root = Path(root)

        def unpack(value):
            if isinstance(value, dict):
                if set(value) == {'$image_blob', 'header'}:
                    digest = value['$image_blob']
                    data = (root / 'blobs' / digest).read_bytes()
                    if hashlib.sha256(data).hexdigest() != digest:
                        raise ValueError('Image archive digest mismatch')
                    return value['header'] + ',' + base64.b64encode(data).decode()
                return {k: unpack(v) for k, v in value.items()}
            if isinstance(value, list):
                return [unpack(v) for v in value]
            return value

        messages = []
        for digest in refs:
            data = (root / 'objects' / digest).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Message archive digest mismatch')
            messages.append(unpack(json.loads(data)))
        return messages

    def record_request(self, parameters):
        from .evidence_context import text_bytes

        record = {k: v for k, v in parameters.items() if k not in {'messages', 'tools'}}
        record.update(format='dojo-request-v2', messages=self.message_refs(parameters['messages']),
                      active_text_bytes=text_bytes(parameters['messages']))
        if 'tools' in parameters:
            record['tools_object'] = self._store('objects', json.dumps(parameters['tools'], sort_keys=True).encode())
        images = [p['image_url']['url'] for m in parameters['messages'] if isinstance(m.get('content'), list)
                  for p in m['content'] if p.get('type') == 'image_url']
        record['image_hashes'] = [hashlib.sha256(url.encode()).hexdigest() for url in images]
        record['unique_images'] = len(set(record['image_hashes']))
        with (self.root / 'requests.jsonl').open('a') as out:
            out.write(json.dumps(record, allow_nan=False) + '\n')
            out.flush()
            os.fsync(out.fileno())
