"""Developer control probe; run in the worker uv Isaac environment."""

import argparse
import json
import os
import sys
import threading
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--robodojo-root", type=Path, required=True)
parser.add_argument("--robodojo-revision", help="Optional full Git commit; verify before GPU imports")
parser.add_argument("--required-submodule", action="append", default=[], metavar="PATH=FULL_COMMIT")
parser.add_argument("--assets", type=Path, required=True)
parser.add_argument("--object-assets", type=Path)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--task", default="fill_egg_holder")
parser.add_argument("--rtx-driver-version-workaround", action="store_true", help="Only for verified Vulkan version encoding bug")
parser.add_argument("--seed-index", type=int, default=0)
parser.add_argument("--run-mode", choices=["development", "frozen"], default="development")
parser.add_argument("--action-interface", choices=["joint", "tcp", "both"], default="joint")
parser.add_argument("--control-hz", type=int, choices=[250], default=250)
parser.add_argument("--replay-episode", type=Path, help="Development-only original seed episode containing recorded native actions")
parser.add_argument("--replay-step", type=int, help="Replay original actions 1..N before exposing ready.json")
parser.add_argument("--calibrate-cameras", action="store_true", help="Development-only isolated camera/marker qualification, then exit")
parser.add_argument("--active-vision-marker", action="store_true", help="Development-only static visual marker for TCP observation qualification")
args = parser.parse_args()
args.robodojo_root = args.robodojo_root.resolve()
args.output = args.output.resolve()
args.output.mkdir(parents=True, exist_ok=False)
app = None
exit_code = 1
try:
    if (args.calibrate_cameras or args.active_vision_marker) and (args.run_mode != 'development' or args.replay_episode is not None):
        raise ValueError('Camera qualification requires an isolated development scene without replay')
    if args.calibrate_cameras and args.active_vision_marker:
        raise ValueError('Select only one isolated camera qualification fixture')
    replay = None
    if (args.replay_episode is None) != (args.replay_step is None):
        raise ValueError('Replay episode and step must be supplied together')
    if args.replay_episode is not None:
        from native_replay import load_replay
        replay = load_replay(args.replay_episode, args.replay_step, args.task, args.seed_index, args.run_mode)
    from runtime_source import parse_required_submodules, verify_runtime_source, write_runtime_provenance

    if args.required_submodule and not args.robodojo_revision:
        raise ValueError("Required submodule pins require --robodojo-revision")
    if args.robodojo_revision:
        provenance = verify_runtime_source(
            args.robodojo_root, args.robodojo_revision,
            parse_required_submodules(args.required_submodule),
        )
    else:
        provenance = {"schema_version": 1, "audience": "developer_only", "status": "unpinned",
                      "path": str(args.robodojo_root), "required_submodules": {}}
    write_runtime_provenance(args.output, provenance)
    sys.path[:0] = [str(args.robodojo_root), str(args.robodojo_root / "XPolicyLab")]
    os.chdir(args.output)
    # Initialize the selected driver before Kit loads its bundled libraries.
    import torch

    torch.cuda.init()
    from isaaclab.app import AppLauncher
    from env.camera_manager.capture.render_sync import zero_delay_kit_args

    kit_args = "--/rtx/verifyDriverVersion/enabled=false" if args.rtx_driver_version_workaround else ""
    kit_args = (kit_args + " " + zero_delay_kit_args()).strip()
    app = AppLauncher(headless=True, enable_cameras=True, kit_args=kit_args).app
    import env.global_configs as paths
    from omegaconf import OmegaConf

    paths.ASSETS_PATH = str(args.assets)
    paths.OBJECTS_PATH = str(args.object_assets or args.assets / "Object/RoboDojo")
    paths.ROBOTS_PATH = str(args.assets / "Robots")
    from src.eval_client import eval_env
    from utils.load_file import load_yaml
    from utils.pipeline_utils import process_config, process_randomization

    class ResetOnlyClient:
        def __init__(self, **kwargs):
            pass

        def call(self, func_name, **kwargs):
            if func_name != "reset":
                raise RuntimeError("Probe client is reset-only")

    eval_env.WsModelClient = ResetOnlyClient
    config_dir = args.robodojo_root / "env_cfg"
    evaluation = load_yaml(str(config_dir / "arx_x5.yml"))
    evaluation['observation']['collect_freq'] = args.control_hz
    evaluation.update(
        task_name=args.task,
        num_envs=1,
        device_id=0,
        eval_batch=False,
        policy_name="astra_control_probe",
        additional_info=args.run_mode,
        seed=0,
    )
    evaluation["observation"]["vision"].update(approximate_depth=False, depth=False, intrinsic_matrix=False, extrinsic_matrix=False)
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
    from egg_harness.control_timing import configure_timing, verify_environment_timing
    timing = configure_timing(cfg)
    (args.output / 'control-timing.json').write_text(json.dumps(timing, indent=2) + '\n')
    cfg.sim.seed = [0]
    env = eval_env.create_eval_env(cfg, app)
    verify_environment_timing(env)
    from env.scene_manager import layout_manager
    from utils.load_file import require_object_metadata

    # Resolve metadata failure at its source instead of upstream's NoneType exception.
    layout_manager.load_object_metadata = require_object_metadata
    if args.seed_index < 0:
        raise ValueError("seed-index must be nonnegative")
    for _ in range(args.seed_index + 1):
        seeds = env.seed_manager.get_seeds(max_count=1)
        if not seeds:
            raise ValueError(f"Layout index {args.seed_index} unavailable for {args.task}")
    env.reset(seed=seeds)
    from native_setup import prepare_native_evaluation
    setup = prepare_native_evaluation(env)
    (args.output / "native-setup.json").write_text(json.dumps(setup))
    if args.calibrate_cameras:
        from calibrate_cameras import run_calibration
        run_calibration(env, args.output)
    else:
        if args.active_vision_marker:
            from active_vision_marker import prepare_marker
            prepare_marker(env, args.output)
        from server_loop import run_server
        run_server(env, args.output, seeds, args.run_mode, args.task, replay=replay, action_interface=args.action_interface)
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
    (args.output / "exit-status.json").write_text(json.dumps({"exit_code": exit_code}))
    # Bound Kit shutdown after durable results; GPU state dies with this process.
    watchdog = threading.Timer(20, os._exit, args=(exit_code,))
    watchdog.daemon = True
    watchdog.start()
    sys.stdout.flush()
    sys.stderr.flush()
    if app is not None:
        app.close()
    os._exit(exit_code)
