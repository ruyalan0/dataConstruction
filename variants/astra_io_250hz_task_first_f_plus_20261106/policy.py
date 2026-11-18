"""New control-only entry point; all changes are confined to this variant directory."""
import argparse
from datetime import datetime, timezone
import json
import traceback
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
HARNESS = HERE / 'harness'
sys.path.insert(0, str(HERE.parents[1] / 'scripts'))
from full_tool_retry_policy import RETRY_POLICY, make_retrying_create

sys.path.insert(0, str(HARNESS))
from egg_harness.prompting import JOINT_SYSTEM, build_system
from egg_harness.observation_pair import PROTOCOL
from egg_harness.clocks import clock_metadata
from egg_harness.io_contract import precision_metadata
from egg_harness.visual_history import visual_history_metadata


def with_api_error_logging(create, trace, call_number, secret):
    """Preserve full API errors without changing requests or retry decisions."""
    trace = Path(trace)

    def scrub(value):
        if isinstance(value, str):
            return value.replace(secret, '[REDACTED]') if secret else value
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    def logged_create(**kwargs):
        try:
            return create(**kwargs)
        except Exception as error:
            response = getattr(error, 'response', None)
            headers = getattr(response, 'headers', {})
            event = scrub(dict(
                timestamp=datetime.now(timezone.utc).isoformat(), call=call_number(),
                type=type(error).__name__, http_status=getattr(error, 'status_code', None),
                message=str(error), response_body=response.text if response is not None else None,
                request_id=getattr(error, 'request_id', None),
                response_headers={name: value for name, value in headers.items()
                                  if name.lower() in ('content-type', 'date', 'server', 'retry-after',
                                                      'x-request-id', 'request-id', 'cf-ray')},
                traceback=traceback.format_exc()))
            try:
                encoded = json.dumps(event, ensure_ascii=True, allow_nan=False) + '\n'
                with (trace / 'api-errors.jsonl').open('a') as stream:
                    stream.write(encoded)
                (trace / 'latest-api-error.json').write_text(encoded)
            except OSError as log_error:
                print('[api-error-log] Could not save error: ' + type(log_error).__name__, flush=True)
            raise

    return logged_create


def install():
    from egg_harness import native_responses
    from egg_harness.storage import check_configured_storage

    original = native_responses.NativeToolVision

    class VariantVision(original):
        def __init__(self, config, trace):
            super().__init__(config, trace)
            if config['tool_ablation']['mode'] != 'control_only' or config['action_interface'] != 'joint':
                raise ValueError('This variant requires control_only and the joint interface')
            if config['transport_retry'] != RETRY_POLICY:
                raise ValueError('Unexpected transport retry settings')
            self.registry.contracts = {'control_arms': self.registry.contracts['control_arms']}
            self.registry.definitions = [tool for tool in self.registry.definitions if tool['name'] == 'control_arms']
            logged_create = with_api_error_logging(
                self.client.responses.create, self.trace, lambda: self.calls, self.provider.api_key)
            self.client.responses.create = make_retrying_create(
                logged_create, self.trace, lambda: self.calls, check_configured_storage)
            path = self.trace / 'native-tools.json'
            record = json.loads(path.read_text())
            record.update(tools=self.registry.definitions, enabled_tools=['control_arms'],
                          history_policy='5s/4fps RGB and previous/current observations; memory in observation',
                          io_variant=HERE.name, scene_feedback_enabled=True, input_protocol=PROTOCOL,
                          model_history_replay=False, memory_location='observation.memory', **clock_metadata(),
                          model_output_duration=False,
                          **precision_metadata(), **visual_history_metadata(),
                          model_output_phase=False, external_transport_retry=RETRY_POLICY)
            path.write_text(json.dumps(record, indent=2) + '\n')

        def ask(self, question, images, system=None):
            return super().ask(question, images, system or build_system([]))

    native_responses.NativeToolVision = VariantVision
    if 'egg_harness.skill_policy' in sys.modules:
        sys.modules['egg_harness.skill_policy'].Vision = VariantVision
    return VariantVision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness', type=Path, default=HARNESS)
    parser.add_argument('--mode', choices=['control_only'], default='control_only')
    args, remaining = parser.parse_known_args()
    if args.harness.resolve() != HARNESS:
        parser.error('Use this variant\'s isolated harness')
    sys.path.insert(0, str(HARNESS))
    install()
    from egg_harness import skill_policy
    sys.argv = [str(Path(__file__).resolve()), *remaining]
    skill_policy.main()


if __name__ == '__main__':
    main()
