"""Native termination, one process/session, and every control-boundary observation."""

import json
import time
from pathlib import Path

from dojo_astra.config import HarnessConfig
from dojo_astra.process_policy import ProcessPolicy, encode_observation


def run_episode(env, output, python, max_steps, max_calls, protocol, image_budget=6, resume=None, harness_profile="evidence", config=None):
    if harness_profile not in {"baseline", "vision", "steps", "feedback", "evidence"}:
        raise ValueError("Unknown harness profile")
    if max_steps is not None or max_calls is not None:
        raise ValueError("Harness limits are disabled; RoboDojo owns episode termination")
    config = HarnessConfig.for_profile(harness_profile, trace_root=str(output / 'agent'),
                                       protocol=protocol, image_budget=image_budget) if config is None else config
    config_path = output / 'effective-config.json'
    config.write(config_path)
    steps = 0
    if resume:
        from dojo_astra.recovery import replay_environment

        steps = replay_environment(env, resume, output)
    policy = ProcessPolicy(
        [
            str(python),
            str(Path(__file__).with_name("policy_process.py")),
            '--config', str(config_path),
        ]
        + (["--resume", str(resume)] if resume else []),
        output / "policy.log",
        timeout=None,
    )
    start = time.monotonic()
    result = {
        "status": "running",
        "development_only": True,
        "protocol": config.protocol,
        "max_steps": max_steps,
        "max_calls": max_calls,
        "image_budget": config.image_budget,
        "harness_profile": harness_profile,
        "task_success": None,
        "recovery_source": str(resume) if resume else None,
    }
    try:
        state = policy.call("observe", obs=encode_observation(env.get_obs()))
        episode = state["episode"]
        while not env.is_episode_end():
            reply = policy.call("act")
            if reply["episode"] != episode:
                raise RuntimeError("Policy episode changed mid-rollout")
            before = env.take_action_cnt[0]
            env.take_action(reply["action"])
            if env.take_action_cnt[0] != before + 1:
                raise RuntimeError("Official action count did not advance")
            steps += 1
            state = policy.call("observe", obs=encode_observation(env.get_obs()), terminated=env.is_episode_end())
            result.update(
                native_steps=steps,
                calls=state["calls"],
                episode=episode,
                elapsed_seconds=time.monotonic() - start,
                last_feedback=state["feedback"],
            )
            checkpoint = output / "checkpoint.tmp"
            checkpoint.write_text(json.dumps(result, indent=2))
            checkpoint.replace(output / "checkpoint.json")
            print(
                json.dumps({"step": steps, "calls": state["calls"], "remaining": state["remaining"]}),
                flush=True,
            )
        state = policy.call('finish')
        result['last_feedback'] = state['feedback']
        ended = env.is_episode_end()
        result.update(
            status="completed",
            stop_reason="official_end" if ended else "development_budget",
            official_ended=ended,
            task_success=bool(env.success[0]) if ended else None,
            native_steps=steps,
            calls=state["calls"],
            elapsed_seconds=time.monotonic() - start,
        )
        if hasattr(env, "get_score"):
            result["observed_progress_score"] = float(env.reward_manager.get_score()[0])
        (output / "episode-result.json").write_text(json.dumps(result, indent=2))
        return result
    except Exception as error:
        result.update(
            status="failed",
            stop_reason="runtime_error",
            error=f"{type(error).__name__}: {error}",
            native_steps=steps,
            elapsed_seconds=time.monotonic() - start,
        )
        (output / "episode-failure.json").write_text(json.dumps(result, indent=2))
        raise
    finally:
        policy.close()
