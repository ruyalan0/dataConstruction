"""Planner-local device selection must preserve configuration and shared defaults."""
from dataclasses import dataclass
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from device_routing import curobo_device_factory, launcher_options, simulator_environment


@dataclass(frozen=True)
class DeviceCfg:
    device: str = 'cuda:0'
    dtype: str = 'float32'
    collision_geometry_dtype: str = 'float32'


class DeviceRoutingTests(unittest.TestCase):
    def test_independent_planners_preserve_shared_constructor_and_other_options(self):
        left = curobo_device_factory(DeviceCfg, 4)
        right = curobo_device_factory(DeviceCfg, 5)
        self.assertEqual(left().device, 'cuda:4')
        self.assertEqual(right().device, 'cuda:5')
        self.assertEqual(DeviceCfg().device, 'cuda:0')
        self.assertEqual(left(dtype='float64'), DeviceCfg(device='cuda:4', dtype='float64'))
        self.assertEqual(left().collision_geometry_dtype, DeviceCfg().collision_geometry_dtype)

    def test_renderer_routing_uses_the_same_ordinal_without_cuda_visibility_remapping(self):
        source = dict(CUDA_VISIBLE_DEVICES='0', KEEP='unchanged')
        environment = simulator_environment(source, 6)
        self.assertNotIn('CUDA_VISIBLE_DEVICES', environment)
        self.assertEqual(environment['ISAACSIM_GPU'], '6')
        self.assertEqual(source['CUDA_VISIBLE_DEVICES'], '0')
        options = launcher_options(6, dict(headless=True, enable_cameras=True, kit_args='--existing'))
        self.assertEqual(options['device'], 'cuda:6')
        self.assertFalse(options['multi_gpu'])
        self.assertTrue(options['headless'] and options['enable_cameras'])
        self.assertIn('--existing', options['kit_args'])
        self.assertIn('/renderer/activeGpu=6', options['kit_args'])

    def test_invalid_gpu_cannot_produce_a_constructor(self):
        for gpu in (-1, True, 4., '4'):
            with self.assertRaises(ValueError):
                curobo_device_factory(DeviceCfg, gpu)


if __name__ == '__main__':
    unittest.main()
