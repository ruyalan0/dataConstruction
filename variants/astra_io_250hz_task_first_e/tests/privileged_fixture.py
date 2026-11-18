"""Small live scene used to exercise capture without importing Isaac."""
from types import SimpleNamespace
import numpy as np


class LayoutFixture:
    def __init__(self):
        self.position = np.array([.1, .2, .3])
        self.quaternion = np.array([1., 0., 0., 0.])
        self.scale = np.ones(3)
        self.instance_type_by_env = [{'target_instance': 'rigid'}]
        self.metadata = dict(model_name='test_object', description='visible test object',
            geometry=dict(oriented_bbox=dict(vertices=[
                [x, y, z] for x in (-.1, .1) for y in (-.2, .2) for z in (-.3, .3)])),
            active=dict(functional=dict(grasp=dict(frame=[[0., 0., .05, 1., 0., 0., 0.]])),
                        support=dict(top=dict(center=[[0., 0., .3, 1., 0., 0., 0.]], radius=[.1]))))
        self.obj = SimpleNamespace(get_local_scale=lambda: self.scale.copy(), usd_path='fixture.usd')

    def get_labels_by_prefix(self, env_idx, prefix):
        return ['target']

    def get_instance_name(self, env_idx, label):
        return 'target_instance'

    def get_scene_object(self, env_idx, instance):
        return self.obj

    def get_instance_metadata(self, **kwargs):
        return self.metadata

    def get_instance_pose(self, env_idx, inst_name, relative):
        assert relative is True
        return self.position.copy(), self.quaternion.copy()

    def get_functional_points(self, **kwargs):
        return [[*list(self.position + [0., 0., .05]), 1., 0., 0., 0.]]

    def get_support_points(self, **kwargs):
        return ([[*list(self.position + [0., 0., .3]), 1., 0., 0., 0.]], [.1])


def attach_scene(native, origin=(0., 0., 0.)):
    layout = LayoutFixture()
    native.scene_manager = SimpleNamespace(layout_manager=layout, env_origins=np.asarray([origin]))
    return layout
