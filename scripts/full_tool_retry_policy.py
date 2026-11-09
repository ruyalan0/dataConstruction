"""Retry only failed API transport calls; run the sealed full-tool policy unchanged."""
import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import sys
import time

from openai import APIConnectionError, APIStatusError


RETRY_POLICY = dict(enabled=True, max_attempts=None, initial_delay_seconds=60,
                    maximum_backoff_seconds=300,
                    retry_http_statuses=[408, 409, 429, 499, '5xx'],
                    connection_and_request_timeouts=True, same_request=True,
                    failed_transport_attempts_consume_decision_budget=False)


def retryable(error):
    return isinstance(error, APIConnectionError) or (
        isinstance(error, APIStatusError) and
        (error.status_code in (408, 409, 429, 499) or 500 <= error.status_code < 600))


def delay_for(error, attempt):
    delay = min(300, 60 * 2 ** min(attempt - 1, 3))
    response = getattr(error, 'response', None)
    if response is not None:
        value = response.headers.get('retry-after')
        if value:
            try:
                delay = max(delay, float(value))
            except ValueError:
                try:
                    delay = max(delay, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
    return delay


def append(path, event):
    with path.open('a') as stream:
        stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()


def make_retrying_create(create, trace, call_number, check_storage, sleep=None):
    trace = Path(trace)
    if sleep is None:
        sleep = time.sleep

    def retrying_create(**kwargs):
        request_hash = hashlib.sha256(json.dumps(kwargs, sort_keys=True, allow_nan=False).encode()).hexdigest()
        logical_call = call_number()
        attempt = 0
        while True:
            check_storage()
            attempt += 1
            started = time.monotonic()
            try:
                result = create(**kwargs)
            except Exception as error:
                if not retryable(error):
                    raise
                delay = delay_for(error, attempt)
                event = dict(event='retry', call=logical_call, attempt=attempt,
                             timestamp=datetime.now(timezone.utc).isoformat(), type=type(error).__name__,
                             http_status=getattr(error, 'status_code', None), request_sha256=request_hash,
                             request_seconds=time.monotonic() - started, retry_after_seconds=delay,
                             next_attempt=attempt + 1, robot_commands_issued=0)
                append(trace / 'api-retries.jsonl', event)
                (trace / 'retry-status.json').write_text(json.dumps(dict(event, state='waiting'), indent=2) + '\n')
                print(f'[api-retry] call={logical_call} attempt={attempt} type={type(error).__name__} '
                      f'http={event["http_status"]} wait={delay:.0f}s; scene and request preserved', flush=True)
                remaining = delay
                while remaining > 0:
                    chunk = min(30, remaining)
                    sleep(chunk)
                    remaining -= chunk
                    check_storage()
                continue
            if attempt > 1:
                event = dict(event='recovered', call=logical_call, attempts=attempt,
                             timestamp=datetime.now(timezone.utc).isoformat(), request_sha256=request_hash,
                             response_id=getattr(result, 'id', None))
                append(trace / 'api-retries.jsonl', event)
                (trace / 'retry-status.json').write_text(json.dumps(dict(event, state='recovered'), indent=2) + '\n')
                print(f'[api-retry] call={logical_call} recovered after {attempt} transport attempts', flush=True)
            return result

    return retrying_create


def install():
    from egg_harness import native_responses
    from egg_harness.storage import check_configured_storage
    original = native_responses.NativeToolVision

    class RetryingVision(original):
        def __init__(self, config, trace):
            super().__init__(config, trace)
            assert config['transport_retry'] == RETRY_POLICY
            self.client.responses.create = make_retrying_create(
                self.client.responses.create, self.trace, lambda: self.calls, check_configured_storage)
            metadata = self.trace / 'native-tools.json'
            data = json.loads(metadata.read_text())
            data['external_transport_retry'] = RETRY_POLICY
            data['retry_wrapper_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            metadata.write_text(json.dumps(data, indent=2) + '\n')

    native_responses.NativeToolVision = RetryingVision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    sys.path.insert(0, str(args.harness.resolve()))
    install()
    from egg_harness import skill_policy
    sys.argv = [str(Path(__file__).resolve()), *remaining]
    skill_policy.main()


if __name__ == '__main__':
    main()
