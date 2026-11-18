"""Mirror official evaluation setup without exposing task internals to policy."""


def prepare_native_evaluation(env):
    env.run_reward()
    if hasattr(env, "get_score"):
        env.get_score()
    queried = []
    if getattr(env, "interact", False) and hasattr(env, "query_support_arm_traj"):
        for env_index in env.get_running_env_idx_list():
            env.query_support_arm_traj(env_idx=env_index)
            queried.append(int(env_index))
    return {"reward_registered": True, "support_trajectory_primed_for": queried}
