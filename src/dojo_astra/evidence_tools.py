"""Model-authored working state and read-only queries over allowed episode evidence."""

import base64
import io
import json
from typing import Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class ToolInputError(ValueError):
    pass


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class FrameRef(Input):
    sequence: int = Field(ge=0, strict=True)
    camera: str = Field(min_length=1)


class Inspect(Input):
    frames: list[StrictInt] = Field(min_length=1, max_length=4)
    cameras: list[str] = Field(min_length=1, max_length=3)
    roi: tuple[StrictInt, StrictInt, StrictInt, StrictInt] | None = Field(default=None, description='Original pixel x0,y0,x1,y1')
    compare: Literal['frames', 'before_after'] = 'frames'
    scale: int = Field(default=1, ge=1, le=2, strict=True)

    @model_validator(mode='after')
    def valid_selection(self):
        if any(type(n) is not int or n < 0 for n in self.frames):
            raise ValueError('Frames must be nonnegative integers')
        if len(set(self.frames)) != len(self.frames) or len(set(self.cameras)) != len(self.cameras):
            raise ValueError('Choose distinct frames and cameras')
        if self.compare == 'before_after' and (len(self.frames) != 2 or self.frames != sorted(self.frames)):
            raise ValueError('before_after requires two chronological frames')
        return self


class Claim(Input):
    claim_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,48}$')
    text: str = Field(min_length=1, max_length=1000)
    model_assessed_status: Literal['hypothesis', 'visually_supported', 'contradicted', 'retired']
    evidence_refs: list[FrameRef] = Field(max_length=8)
    supersedes: list[str] = Field(default_factory=list, max_length=8)


class Remember(Input):
    subgoal: str = Field(max_length=1000)
    updates: list[Claim] = Field(max_length=16)


class History(Input):
    start: int = Field(default=0, ge=0, strict=True)
    limit: int = Field(default=8, ge=1, le=20, strict=True)
    kinds: list[Literal['decision', 'feedback', 'observation', 'native_action', 'memory_update', 'tool_error']] = (
        Field(default_factory=lambda: ['decision', 'feedback'], min_length=1, max_length=6)
    )


SCHEMAS = {'inspect': Inspect, 'remember': Remember, 'history': History}


class EvidenceTools:
    def __init__(self, trace):
        self.trace = trace
        self.subgoal = ''
        self.claims = {}
        self.known_ids = set()

    def snapshot(self):
        return {'subgoal': self.subgoal, 'active_claims': list(self.claims.values()),
                'scope': 'Model assessments only. Reference validity does not verify a claim.'}

    def restore(self, events):
        for event in events:
            if event['kind'] == 'memory_update':
                command = self.validate('remember', event['data'])
                self.claims = self._updated(command)
                self.subgoal = command.subgoal
                self.known_ids.update(c.claim_id for c in command.updates)

    def _frame(self, sequence, camera):
        if sequence not in self.trace.frames:
            raise ToolInputError(f'Frame {sequence} unavailable; available 0..{max(self.trace.frames)}')
        if camera not in self.trace.frames[sequence]:
            raise ToolInputError(f'Camera unavailable; choose {list(self.trace.frames[sequence])}')
        return self.trace.frames[sequence][camera]

    def validate(self, name, arguments):
        command = SCHEMAS[name].model_validate(arguments)
        if name == 'inspect':
            for seq in command.frames:
                for camera in command.cameras:
                    path = self._frame(seq, camera)
                    with Image.open(path) as image:
                        if command.roi is not None:
                            x0, y0, x1, y1 = command.roi
                            if not 0 <= x0 < x1 <= image.width or not 0 <= y0 < y1 <= image.height:
                                raise ToolInputError(f'ROI must be inside {image.size} original pixels')
        elif name == 'remember':
            for claim in command.updates:
                for ref in claim.evidence_refs:
                    self._frame(ref.sequence, ref.camera)
            self._updated(command)  # Validate the complete transaction before applying any mutation.
        return command

    def _updated(self, command):
        if len(command.model_dump_json().encode()) > 8192:
            raise ToolInputError('Memory updates exceed 8192 bytes; split the update')
        claims = dict(self.claims)
        ids = [c.claim_id for c in command.updates]
        if len(ids) != len(set(ids)):
            raise ToolInputError('Each claim may be updated once per operation')
        for claim in command.updates:
            for old in claim.supersedes:
                if old not in self.known_ids or old in ids:
                    raise ToolInputError('supersedes must reference an earlier claim, not this update')
                claims.pop(old, None)
            if claim.model_assessed_status in {'contradicted', 'retired'}:
                claims.pop(claim.claim_id, None)
            else:
                claims[claim.claim_id] = claim.model_dump()
        if len(claims) > 16 or len(json.dumps({'subgoal': command.subgoal, 'claims': claims}).encode()) > 8192:
            raise ToolInputError('Working state exceeds 16 active claims or 8192 bytes; retire/supersede claims')
        return claims

    def execute(self, name, command):
        if name == 'remember':
            self.claims = self._updated(command)
            self.subgoal = command.subgoal
            self.known_ids.update(c.claim_id for c in command.updates)
            self.trace.event('memory_update', command.model_dump())
            return self.snapshot(), []
        if name == 'history':
            records, cursor = [], command.start
            with (self.trace.root / 'events.jsonl').open() as source:
                for index, line in enumerate(source):
                    if index < command.start:
                        continue
                    record = json.loads(line)
                    if record['kind'] in command.kinds:
                        candidate = {'event_index': index, **record}
                        if len(json.dumps([*records, candidate]).encode()) > 12288:
                            if not records:
                                raise ToolInputError('Single history record exceeds 12288-byte response budget')
                            cursor = index
                            break
                        records.append(candidate)
                    cursor = index + 1
                    if len(records) == command.limit:
                        break
            return {'records': records, 'next_start': cursor, 'scope': 'Archived evidence, not current claims'}, []
        metadata, contents = [], []
        for camera in command.cameras:
            crops, panels = [], []
            offset = 0
            for sequence in command.frames:
                with Image.open(self._frame(sequence, camera)) as original:
                    roi = command.roi or (0, 0, original.width, original.height)
                    crop = original.crop(roi).convert('RGB')
                    crop = crop.resize((crop.width * command.scale, crop.height * command.scale))
                    panels.append({'sequence': sequence, 'camera': camera, 'roi': roi,
                                   'original_size': original.size, 'display_size': crop.size,
                                   'display_offset': [offset, 20], 'scale': command.scale,
                                   'observed_robot_state': self.trace.frame_states[sequence]})
                    crops.append(crop)
                    offset += crop.width
            sheet = Image.new('RGB', (offset, max(c.height for c in crops) + 20), 'white')
            draw = ImageDraw.Draw(sheet)
            for crop, panel in zip(crops, panels, strict=True):
                x, y = panel['display_offset']
                sheet.paste(crop, (x, y))
                draw.text((x, 2), f"F{panel['sequence']} {camera}", fill='black')
            buffer = io.BytesIO()
            sheet.save(buffer, format='PNG')
            data = buffer.getvalue()
            digest = self.trace._store('blobs', data)
            contents.append({'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' +
                                                               base64.b64encode(data).decode()}})
            metadata.append({'camera': camera, 'panels': panels, 'derived_image_sha256': digest})
        return {'inspection': metadata, 'compare': command.compare,
                'scope': 'Crops only resample original pixels. Camera motion also causes image displacement.'}, contents
