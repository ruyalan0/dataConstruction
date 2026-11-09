"""Allocate independent controllers across Astra gateways, without concurrency caps."""
from dataclasses import dataclass, field
import fcntl
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .vision import load_credentials


PROVIDERS = ('azure-astra', 'zzz-astra', 'zzy-astra')
AUTO_PROVIDERS = ('azure-astra', 'zzz-astra')

def provider_url(provider, config):
    value = config['provider_base_urls'][provider]
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.hostname.endswith('.invalid')
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError('Configure an explicit HTTPS endpoint without credentials: ' + provider)
    return value


def credential(provider, config):
    if provider == 'azure-astra':
        key = load_credentials(config['credentials'])['api_key']
    elif provider in ('zzz-astra', 'zzy-astra'):
        setting = 'zzz_credentials' if provider == 'zzz-astra' else 'zzy_credentials'
        text = Path(config[setting]).read_text()
        keys = re.findall(r'(?m)^\s*(sk-[A-Za-z0-9_-]+)\s*$', text)
        if len(keys) != 1:
            raise ValueError(f'Expected exactly one standalone API key in {provider} credential file')
        key = keys[0]
    else:
        raise ValueError('Unknown Astra provider')
    if not isinstance(key, str) or not key.strip() or key != key.strip():
        raise ValueError('Invalid Astra API key')
    return key


def next_allocation(path):
    """NAS-shared round robin; the file lock covers allocation only, never API calls."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        raw = stream.read()
        if raw and re.fullmatch(r'[0-9]+\n', raw) is None:
            raise ValueError('Corrupt Astra allocation sequence; inspect before resetting')
        index = int(raw) if raw else 0
        stream.seek(0)
        stream.truncate()
        stream.write(str(index + 1) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
        return index


@dataclass(frozen=True)
class Provider:
    name: str
    allocation: int | None
    api_key: str = field(repr=False)
    base_url: str

    def headers(self, session_id):
        # ModelHub's stickiness header is specific to that gateway.
        return {'extra': json.dumps({'session_id': session_id})} if self.name == 'azure-astra' else {}

    def audit(self):
        return {'provider': self.name, 'base_url': self.base_url, 'provider_allocation': self.allocation}


def select_provider(config):
    requested = config.get('astra_provider', 'auto')
    if requested not in ('auto', *PROVIDERS):
        raise ValueError('astra_provider must be auto or one of: ' + ', '.join(PROVIDERS))
    # Validate both configured credentials before advancing the shared sequence.
    names = AUTO_PROVIDERS if requested == 'auto' else (requested,)
    urls = {name: provider_url(name, config) for name in names}
    keys = {name: credential(name, config) for name in names}
    index = next_allocation(config['astra_sequence_file']) if requested == 'auto' else None
    name = AUTO_PROVIDERS[index % len(AUTO_PROVIDERS)] if index is not None else requested
    return Provider(name, index, keys[name], urls[name])
