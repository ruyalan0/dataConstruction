"""Developer control probe; run in the worker uv Isaac environment."""

import argparse
import json
import os
import sys
import threading
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--robodojo-root", type=Path, required=True)
parser.add_argument("--assets", type=Path, required=True)
parser.add_argument("--object-assets", type=Path)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--astra-python", type=Path)
parser.add_argument("--mode", choices=["calibration", "interface", "episode"], default="calibration")
parser.add_argument("--max-steps", type=int, default=0, help="Must be 0: native termination only")
parser.add_argument("--max-calls", type=int, default=0)
parser.add_argument("--image-budget", type=int, default=6)
parser.add_argument("--task", default="align_blocks")
parser.add_argument("--resume", type=Path)
parser.add_argument("--protocol", choices=["tools", "json-high"], default="json-high")
parser.add_argument("--harness-profile", choices=["baseline", "vision", "steps", "feedback", "evidence"], default="evidence")
parser.add_argument("--rtx-driver-version-workaround", action="store_true", help="Only for verified Vulkan version encoding bug")
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
app = None
exit_code = 1
try:
    from freeze_environment import freeze_environment

    freeze_environment(args)
    sys.path[:0] = [str(args.robodojo_root), str(args.robodojo_root / "XPolicyLab")]
    os.chdir(args.output)
    # Initialize the selected driver before Kit loads its bundled libraries.
    import torch

    torch.cuda.init()
    from isaaclab.app import AppLauncher

    kit_args = "--/rtx/verifyDriverVersion/enabled=false" if args.rtx_driver_version_workaround else ""
    app = AppLauncher(headless=True, enable_cameras=True, kit_args=kit_args).app
    import env.global_configs as paths
    from omegaconf import OmegaConf

    paths.ASSETS_PATH = str(args.assets)
    paths.OBJECTS_PATH = str(args.object_assets or args.assets / "Object/RoboDojo")
    paths.ROBOTS_PATH = str(args.assets / "Robots")
    from src.eval_client import eval_env
    from utils.load_file import load_yaml
    from utils.pipeline_utils import process_config, process_randomization

    from dojo_astra.arx_x5_interpreter import Decision
    from dojo_astra.executor import Executor
    from dojo_astra.observation_adapter import adapt
    from dojo_astra.trace_store import TraceStore

    class ResetOnlyClient:
        def __init__(self, **kwargs):
            pass

        def call(self, func_name, **kwargs):
            if func_name != "reset":
                raise RuntimeError("Probe client is reset-only")

    eval_env.WsModelClient = ResetOnlyClient
    config_dir = args.robodojo_root / "env_cfg"
    evaluation = load_yaml(str(config_dir / "arx_x5.yml"))
    evaluation.update(
        task_name=args.task,
        num_envs=1,
        device_id=0,
        eval_batch=False,
        policy_name="astra_control_probe",
        additional_info="development_only",
        seed=0,
    )
    cfg = {
        k: load_yaml(str(config_dir / k / (evaluation["config"][k] + ".yml")))
        for k in ("sim", "scene", "camera", "robot")
    }
    cfg.update(
        task_env=load_yaml(str(args.robodojo_root / f"task/RoboDojo/config/{args.task}.yml")),
        eval_cfg=evaluation,
        deploy_cfg={"policy_name": "astra_control_probe", "port": 19099},
    )
    cfg = OmegaConf.create(cfg)
    cfg.sim.scene.num_envs = 1
    cfg = process_randomization(cfg)
    cfg, _ = process_config(cfg, task_name=args.task)
    cfg.eval_cfg.eval_num = 1
    cfg.camera.default_frequency = cfg.eval_cfg.observation.collect_freq
    cfg.sim.seed = [0]
    env = eval_env.create_eval_env(cfg, app)
    from env.scene_manager import layout_manager
    from utils.load_file import require_object_metadata

    # Resolve metadata failure at its source instead of upstream's NoneType exception.
    layout_manager.load_object_metadata = require_object_metadata
    seeds = env.seed_manager.get_seeds(max_count=1)
    if not seeds:
        raise ValueError(f"No evaluation layouts available for {args.task}")
    env.reset(seed=seeds)
    env.run_reward()  # Official run_eval registers checks before the first action.
    if hasattr(env, "get_score"):
        env.get_score()  # Register official partial-progress checks, never sent to policy.
    if args.mode == 'interface':
        from check_motion_interface import run_interface_probe

        interface_result = run_interface_probe(env, args.output)
        print(json.dumps(interface_result), flush=True)
        exit_code = 0
        raise SystemExit(0)
    if args.mode == "episode":
        from run_episode_loop import run_episode

        if args.astra_python is None or args.max_steps != 0 or args.max_calls != 0:
            raise ValueError("Episode mode requires API interpreter and native termination without harness caps")
        episode_result = run_episode(
            env,
            args.output,
            args.astra_python,
            None,
            None if args.max_calls == 0 else args.max_calls,
            args.protocol,
            args.image_budget,
            args.resume,
            args.harness_profile,
        )
        print(json.dumps(episode_result), flush=True)
        exit_code = 0
        raise SystemExit(0)
    trace, executor = TraceStore(args.output, "control"), Executor()
    sequence = 0
    feedback = []
    for amount in (0, 5):
        obs = adapt(env.get_obs(), sequence)
        trace.observation(obs)
        executor.observe(obs)
        hold = {"operation": "hold", "frame": "world", "axis": "+z", "amount": 0, "gripper": "hold"}
        left = dict(hold, operation="translate" if amount else "hold", amount=amount)
        executor.load(Decision(left=left, right=hold, steps=4, note="control calibration"))
        while executor.remaining:
            if env.is_episode_end():
                raise RuntimeError("Official episode ended before control calibration completed")
            before_count = env.take_action_cnt[0]
            env.take_action(executor.next_action())
            if env.take_action_cnt[0] != before_count + 1:
                raise RuntimeError("Official environment did not execute the requested action")
            sequence += 1
            obs = adapt(env.get_obs(), sequence)
            trace.observation(obs)
            executor.observe(obs)
            trace.event("feedback", executor.feedback)
        feedback.append(executor.feedback)
        sequence += 1
    result = {"status": "completed", "development_only": True, "feedback": feedback}
    if args.astra_python:
        import base64
        import subprocess

        obs = adapt(env.get_obs(), sequence + 1)
        trace.observation(obs)
        state = {}
        for arm in ("left", "right"):
            state[f"{arm}_ee_pose"] = obs.poses[arm].tolist()
            state[f"{arm}_ee_joint_state"] = [obs.gripper_commands[arm]]
        request = {
            "data_format_version": "v1.0",
            "instruction": obs.instruction,
            "state": state,
            "vision": {
                name: {"color": base64.b64encode(path.read_bytes()).decode()}
                for name, path in trace.frames[obs.sequence].items()
            },
        }
        request_path, response_path = args.output / "astra-request.json", args.output / "astra-response.json"
        request_path.write_text(json.dumps(request))
        subprocess.run(
            [
                str(args.astra_python),
                str(Path(__file__).with_name("decide_file.py")),
                "--request",
                str(request_path),
                "--response",
                str(response_path),
                "--trace",
                str(args.output / "astra"),
            ],
            check=True,
            timeout=180,
        )
        response = json.loads(response_path.read_text())
        before_count = env.take_action_cnt[0]
        if env.is_episode_end():
            raise RuntimeError("Episode ended before Astra action")
        env.take_action(response["action"])
        if env.take_action_cnt[0] != before_count + 1:
            raise RuntimeError("Astra action was not executed")
        after = adapt(env.get_obs(), obs.sequence + 1)
        trace.observation(after)
        result["astra"] = {
            "executed_native_steps": 1,
            "requested_segment_steps": response["steps"],
            "displacement_mm": {
                arm: ((after.poses[arm][:3] - obs.poses[arm][:3]) * 1000).tolist()
                for arm in ("left", "right")
            },
        }
    (args.output / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
    exit_code = 0
except SystemExit:
    if exit_code != 0:
        raise
except BaseException:  # noqa: BLE001 -- persist failure before Isaac shutdown
    import traceback

    error = traceback.format_exc()
    (args.output / "error.txt").write_text(error)
    print(error, flush=True)
finally:
    sys.stdout.flush()
    sys.stderr.flush()
    (args.output / "exit-status.json").write_text(json.dumps({"exit_code": exit_code}))
    # Bound Kit shutdown after durable results; GPU state dies with this process.
    watchdog = threading.Timer(20, os._exit, args=(exit_code,))
    watchdog.daemon = True
    watchdog.start()
    if app is not None:
        app.close()
    os._exit(exit_code)
