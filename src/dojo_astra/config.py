"""One validated configuration shared by the official and process adapters."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class HarnessConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    action_type: Literal['ee'] = 'ee'
    env_cfg_type: Literal['arx_x5'] = 'arx_x5'
    credentials_example: str = '/workspace/APIs/Azure-gpt-astra.py'
    trace_root: str
    model: str = Field(default='gpt-6-astra', min_length=1)
    protocol: Literal['json-high', 'tools'] = 'json-high'
    image_budget: int = Field(default=6, ge=3, le=12, strict=True)
    pin_current: bool = True
    report_steps: bool = True
    detailed_feedback: bool = True
    compact_evidence: bool = True
    max_tool_repairs: int = Field(default=3, ge=0, le=10, strict=True)
    context_bytes: int = Field(default=65536, ge=32768, le=262144, strict=True)
    ledger_bytes: int = Field(default=8192, ge=0, le=16384, strict=True)
    stall_boundaries: int = Field(default=3, ge=2, le=8, strict=True)

    @model_validator(mode='after')
    def check_protocol(self):
        if self.compact_evidence and self.protocol != 'json-high':
            raise ValueError('Evidence compaction requires json-high')
        return self

    @classmethod
    def for_profile(cls, profile, **values):
        if profile not in {'baseline', 'vision', 'steps', 'feedback', 'evidence'}:
            raise ValueError('Unknown harness profile')
        return cls(pin_current=profile != 'baseline', report_steps=profile in {'steps', 'feedback', 'evidence'},
                   detailed_feedback=profile in {'feedback', 'evidence'}, compact_evidence=profile == 'evidence',
                   **values)

    def create_agent(self, client=None):
        from .agent import Agent
        from .azure_session import from_example

        options = self.model_dump(exclude={'action_type', 'env_cfg_type', 'credentials_example'})
        agent = Agent(from_example(self.credentials_example) if client is None else client, **options)
        agent.effective_config = self.model_dump()
        agent.freeze_config()
        return agent

    def write(self, path):
        Path(path).write_text(self.model_dump_json(indent=2) + '\n')
