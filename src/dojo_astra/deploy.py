"""Single-environment XPolicyLab deployment entry point."""


def eval_one_episode(TASK_ENV, model_client):
    model_client.call(func_name="reset")
    model_client.call(func_name="update_obs", obs=TASK_ENV.get_obs())
    while not TASK_ENV.is_episode_end():
        actions = model_client.call(func_name="get_action")
        if len(actions) != 1:
            raise ValueError("Exactly one native action per control boundary")
        TASK_ENV.take_action(actions[0])
        model_client.call(func_name="update_obs", obs=TASK_ENV.get_obs(), terminated=TASK_ENV.is_episode_end())
    model_client.call(func_name="finish")


class Model:
    def __init__(self, model_cfg):
        from .config import HarnessConfig

        self.config = HarnessConfig.model_validate(model_cfg)
        self.agent = self.config.create_agent()

    def reset(self):
        self.agent.reset()

    def update_obs(self, obs, terminated=False):
        self.agent.update_obs(obs, terminated=terminated)

    def get_action(self):
        return self.agent.get_action()

    def finish(self):
        return self.agent.finish()
