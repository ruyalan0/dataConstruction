"""One self-contained observation-pair request; responses never become API history."""
import base64
import hashlib
import io
import json
import time
import uuid
from pathlib import Path

from openai import OpenAI
from PIL import Image

from .astra_api import select_provider
from .native_tools import ToolRegistry
from .storage import check_configured_storage
from .vision import failure_record
from .io_contract import readable_input, round_output, dumps_four_decimals, precision_metadata
from .observation_pair import PROTOCOL, build_visual_input
from .visual_history import visual_history_metadata
from .prompting import TOOL_INSTRUCTIONS, build_system
from .clocks import clock_metadata



class NativeToolVision:
    def __init__(self, config, trace):
        effort = config['reasoning_effort']
        if effort not in ('low', 'medium', 'high', 'xhigh'):
            raise ValueError('Astra reasoning effort must be low, medium, high or xhigh')
        self.registry = ToolRegistry(config)
        if config.get('action_interface') != 'joint' or config.get('tool_ablation', {}).get('mode') != 'control_only':
            raise ValueError('Observation-pair input supports joint control_arms only')
        self.registry.contracts = {'control_arms': self.registry.contracts['control_arms']}
        self.registry.definitions = [tool for tool in self.registry.definitions if tool['name'] == 'control_arms']
        self.config = config
        self.trace = Path(trace)
        self.trace.mkdir(parents=True, exist_ok=False)
        self.calls = 0
        self.pending = None
        if (config.get('model_history_replay') is not False
                or config.get('input_protocol') != PROTOCOL):
            raise ValueError(f'Use {PROTOCOL} with model_history_replay=false')
        if any(config.get(key) != value for key, value in clock_metadata().items()):
            raise ValueError('Use the current latched-goal runtime configuration')
        if any(config.get(key) != value for key, value in precision_metadata().items()):
            raise ValueError('Use four-decimal model input and output configuration')
        if any(config.get(key) != value for key, value in visual_history_metadata().items()):
            raise ValueError('Use 5 second / 4 fps RGB history configuration')
        self.last_observation = None
        self.last_memory = None
        self.last_images = {}
        self.seen_call_ids = set()
        self.session_id = uuid.uuid4().hex
        self.provider = select_provider(config)
        self.client = OpenAI(base_url=self.provider.base_url, api_key=self.provider.api_key,
                             max_retries=0, timeout=config['request_timeout'])
        (self.trace / 'native-tools.json').write_text(json.dumps({
            'api': 'responses', **self.provider.audit(), 'tools': self.registry.definitions,
            'session_id': self.session_id, 'reasoning_effort': effort,
            'history_policy': '5s/4fps RGB and previous/current decision observations; memory in observation',
            'input_protocol': PROTOCOL, 'model_history_replay': False, **clock_metadata(), **precision_metadata(),
            **visual_history_metadata(), 'parallel_tool_calls': False, 'max_retries': 0}, indent=2))

    def observe(self, feedback, step, ended):
        """Record a completed execution, including zero-motion validation rejection."""
        if self.pending is None:
            return
        result = {'feedback': feedback, 'step': step, 'ended': ended}
        item = {'type': 'function_call_output', 'call_id': self.pending['call_id'],
                'output': json.dumps(result, allow_nan=False)}
        self.pending['stem'].with_suffix('.tool-result.json').write_text(json.dumps(item, indent=2))
        # Commit only the measured pre-action snapshot, never a response/tool cycle.
        self.last_observation = self.pending['snapshot']
        self.last_memory = self.pending['memory']
        self.pending = None

    def fail_pending(self, error, step):
        """Audit an aborted call without retrying or claiming that no action occurred."""
        if self.pending is not None:
            self.pending['stem'].with_suffix('.tool-result.json').write_text(json.dumps({
                'call_id': self.pending['call_id'], 'status': 'aborted', 'step': step,
                'error_type': type(error).__name__, 'message': str(error),
                'scope': 'Execution may be partial; inspect robot records. No automatic retry.'}, indent=2))
            self.pending = None

    def ask(self, question, images, system=None):
        check_configured_storage()
        if self.pending is not None:
            raise RuntimeError('Previous tool has no execution result')
        if self.calls >= self.config['max_model_calls']:
            raise TimeoutError('Model call budget exhausted')
        context = json.loads(question)
        if self.last_memory is not None and context.get('memory') != self.last_memory:
            raise ValueError('observation.memory must equal the latest executed decision memory')
        context, images, current_snapshot = build_visual_input(context, images, self.last_observation)
        question = dumps_four_decimals(context)
        content = [{'type': 'input_text', 'text': question}]
        manifest = []
        for name, path in images.items():
            raw_image = Path(path).read_bytes()
            with Image.open(io.BytesIO(raw_image)) as picture:
                mime = {'JPEG': 'image/jpeg', 'PNG': 'image/png'}[picture.format]
                manifest.append({'name': name, 'path': str(Path(path).resolve()), 'size': picture.size,
                                 'sha256': hashlib.sha256(raw_image).hexdigest()})
            content.extend([{'type': 'input_text', 'text': name},
                            {'type': 'input_image', 'image_url': f'data:{mime};base64,' +
                             base64.b64encode(raw_image).decode(), 'detail': 'high'}])
        self.last_images = {item['name']: item for item in manifest}
        observation = {'role': 'user', 'content': content}
        instructions = TOOL_INSTRUCTIONS + '\n' + (system or build_system([]))
        self.calls += 1
        stem = self.trace / f'{self.calls:04d}'
        # Preserve request/response artifacts for auditing and cost accounting.
        stem.with_suffix('.request.json').write_text(json.dumps({
            'system': instructions, 'question': question, 'images': manifest,
            'model': self.config['model'], 'reasoning_effort': self.config['reasoning_effort'],
            'api': 'responses', **self.provider.audit(), 'tools': list(self.registry.contracts),
            'replayed_call_id': None, 'model_history_replay': False, 'input_protocol': PROTOCOL,
            'session_id': self.session_id, 'step': context['observation']['step'],
            'memory_location': 'observation.memory', **clock_metadata(),
            **precision_metadata(), **visual_history_metadata(),
            'model_output_phase': False, 'model_output_duration': False}))
        stem.with_suffix('.api-input.readable.json').write_text(json.dumps(
            {'scope': 'Audit view of actual input; image bytes and encrypted reasoning replaced',
             'input': readable_input([observation])}, indent=2))
        start = time.monotonic()
        try:
            response = self.client.responses.create(
                model=self.config['model'], instructions=instructions,
                input=[observation], tools=self.registry.definitions,
                tool_choice='required', parallel_tool_calls=False, store=False,
                reasoning={'effort': self.config['reasoning_effort']},
                extra_headers=self.provider.headers(self.session_id))
            raw = response.model_dump(exclude_none=True)
            stem.with_suffix('.response.raw.json').write_text(json.dumps(raw, indent=2))
            if response.usage is not None:
                stem.with_suffix('.cost.json').write_text(json.dumps({
                    'seconds': time.monotonic() - start, 'usage': response.usage.model_dump(),
                    'model': self.config['model'], 'returned_model': response.model, 'api': 'responses',
                    **self.provider.audit()}))
            if response.status != 'completed':
                raise RuntimeError(f'Incomplete native response: {response.status}')
            if any(item['type'] not in ('reasoning', 'message', 'function_call') for item in raw['output']):
                raise ValueError('Unsupported native output item')
            calls = [item for item in raw['output'] if item['type'] == 'function_call']
            if len(calls) != 1:
                raise ValueError('Expected exactly one native function call')
            call = calls[0]
            if call.get('status') != 'completed':
                raise ValueError('Incomplete function call')
            call_id = call['call_id']
            if not isinstance(call_id, str) or not call_id or call_id in self.seen_call_ids:
                raise ValueError('Missing or repeated function call ID')
            decision = self.registry.decision(call['name'], call['arguments'])
            # Validate original bounds before rounding. Keep the provider response
            # separately; execute and log the same normalized action values.
            original_arguments = json.loads(call['arguments'])
            # Preserve null fields and schema-declared float types, including
            # integer-looking joint/gripper values, in the effective JSON.
            validated_arguments = self.registry.contracts[call['name']][0].model_validate_json(call['arguments']).model_dump()
            normalized_arguments = round_output(validated_arguments)
            normalized_text = dumps_four_decimals(normalized_arguments)
            call['arguments'] = normalized_text
            stem.with_suffix('.response.json').write_text(json.dumps(raw, indent=2))
            stem.with_suffix('.output-normalization.json').write_text(json.dumps(
                {'decimal_places': 4, 'rounding': 'ROUND_HALF_UP',
                 'arguments_changed': original_arguments != normalized_arguments,
                 'serialization_changed': json.dumps(original_arguments) != normalized_text,
                 'float_format': 'fixed_4_decimal_places',
                 'raw_response': stem.with_suffix('.response.raw.json').name,
                 'effective_response': stem.with_suffix('.response.json').name}, indent=2))
            self.seen_call_ids.add(call_id)
            self.pending = {'call_id': call_id, 'stem': stem,
                            'snapshot': current_snapshot, 'memory': normalized_arguments['memory']}
            return decision
        except BaseException as error:
            stem.with_suffix('.failure.json').write_text(json.dumps({
                **failure_record(error, time.monotonic() - start), **self.provider.audit()}))
            raise
