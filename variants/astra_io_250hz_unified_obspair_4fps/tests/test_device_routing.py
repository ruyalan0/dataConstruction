"""Check native Isaac GPU selection without importing a CUDA or Isaac runtime."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device_routing import launcher_options, simulator_environment


class RoutingTests(unittest.TestCase):
    def test_nonzero_gpu_uses_isaacsim_gpu_without_remapping(self):
        original = {'CUDA_VISIBLE_DEVICES': 'some-other-device', 'KEEP': 'value'}
        result = simulator_environment(original, 4)
        self.assertEqual(result['ISAACSIM_GPU'], '4')
        self.assertNotIn('CUDA_VISIBLE_DEVICES', result)
        self.assertEqual(result['KEEP'], 'value')
        self.assertIn('CUDA_VISIBLE_DEVICES', original)

    def test_simulation_and_renderer_choose_same_native_gpu(self):
        original = {'headless': True, 'kit_args': '--existing=1'}
        result = launcher_options(4, original)
        self.assertEqual(result['device'], 'cuda:4')
        self.assertFalse(result['multi_gpu'])
        self.assertIn('--/renderer/activeGpu=4', result['kit_args'])
        self.assertIn('--existing=1', result['kit_args'])
        self.assertNotIn('device', original)


if __name__ == '__main__':
    unittest.main()
