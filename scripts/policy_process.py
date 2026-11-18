"""One persistent Astra episode; stdout contains only protocol responses."""

import argparse
import json
import sys
import traceback

from dojo_astra.config import HarnessConfig
from dojo_astra.process_policy import decode_observation

parser = argparse.ArgumentParser()
parser.add_argument('--config')
parser.add_argument("--trace")
parser.add_argument("--protocol", choices=["tools", "json-high"])
parser.add_argument('--model', default='gpt-6-astra')
parser.add_argument('--credentials-example', default=HarnessConfig.model_fields['credentials_example'].default)
parser.add_argument("--max-calls", type=int, default=0)
parser.add_argument("--image-budget", type=int, default=6)
parser.add_argument("--resume", type=str)
parser.add_argument("--pin-current", action="store_true")
parser.add_argument("--report-steps", action="store_true")
parser.add_argument("--detailed-feedback", action="store_true")
parser.add_argument("--compact-evidence", action="store_true")
args = parser.parse_args()
if args.max_calls != 0:
    raise ValueError('RoboDojo owns episode termination; no model-call ceiling')
if args.config:
    from pathlib import Path

    if any(flag != '--resume' and flag != '--config' for flag in sys.argv[1:] if flag.startswith('--')):
        raise ValueError('A frozen config cannot be mixed with CLI config overrides')
    config = HarnessConfig.model_validate_json(Path(args.config).read_text())
else:
    config = HarnessConfig(trace_root=args.trace, protocol=args.protocol, model=args.model,
                           credentials_example=args.credentials_example, image_budget=args.image_budget,
                           pin_current=args.pin_current, report_steps=args.report_steps,
                           detailed_feedback=args.detailed_feedback, compact_evidence=args.compact_evidence)
agent = config.create_agent()
if args.resume:
    from dojo_astra.recovery import restore_agent

    restore_agent(agent, args.resume)
expected = 0
for line in sys.stdin:
    request = json.loads(line)
    try:
        if request["id"] != expected:
            raise ValueError("Unexpected request sequence")
        expected += 1
        operation = request["operation"]
        if operation == "observe":
            agent.update_obs(decode_observation(request["obs"]), terminated=request.get('terminated', False))
            result = {
                "feedback": agent.executor.feedback,
                "calls": agent.session.calls,
                "remaining": agent.executor.remaining,
                "episode": agent.episode,
            }
        elif operation == "act":
            action = agent.get_action()[0]
            result = {
                "action": {k: v.tolist() for k, v in action.items()},
                "calls": agent.session.calls,
                "episode": agent.episode,
            }
        elif operation == 'finish':
            result = agent.finish()
        else:
            raise ValueError("Unsupported operation")
        print(json.dumps({"id": request["id"], "ok": True, "result": result}), flush=True)
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        print(
            json.dumps({"id": request["id"], "ok": False, "error": f"{type(error).__name__}: {error}"}),
            flush=True,
        )
        raise SystemExit(1) from error
