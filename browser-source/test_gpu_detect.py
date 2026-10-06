import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from gpu_detect import pci_bus_id, selected_bus, require_nvenc

class GPUDetectionTests(unittest.TestCase):
    def test_hex_bus_becomes_decimal(self):
        self.assertEqual(pci_bus_id('00000000:13:00.0'), 'PCI:19:0:0')
        self.assertEqual(pci_bus_id('0001:af:02.1'), 'PCI:175@1:2:1')
    def test_explicit_gpu_only(self):
        for selection in ('all', '', '0', 'GPU-abcd,GPU-1234'):
            with self.assertRaises(ValueError): selected_bus({'NVIDIA_VISIBLE_DEVICES':selection})
    def test_queries_selected_gpu(self):
        run = Mock(return_value=SimpleNamespace(stdout='00000000:01:00.0\n'))
        self.assertEqual(selected_bus({'NVIDIA_VISIBLE_DEVICES':'GPU-abcd'}, run), 'PCI:1:0:0')
        self.assertIn('--id=GPU-abcd', run.call_args.args[0])
    def test_override_and_invalid_bus(self):
        self.assertEqual(selected_bus({'NVIDIA_XORG_BUS_ID':'PCI:7:0:0'}), 'PCI:7:0:0')
        with self.assertRaises(ValueError): selected_bus({'NVIDIA_XORG_BUS_ID':'anything'})
        with self.assertRaises(ValueError): pci_bus_id('not a GPU')
    def test_runtime_selector_handoff_and_no_ambiguous_fallback(self):
        run = Mock(return_value=SimpleNamespace(stdout='00000000:13:00.0\n'))
        self.assertEqual(selected_bus({'BROWSER_GPU_UUID':'GPU-abcd'}, run), 'PCI:19:0:0')
        self.assertEqual(selected_bus({}, run), 'PCI:19:0:0')
        run.return_value.stdout = '00000000:13:00.0\n00000000:14:00.0\n'
        with self.assertRaises(ValueError): selected_bus({}, run)
    def test_preflight_fails_instead_of_claiming_acceleration(self):
        with self.assertRaisesRegex(RuntimeError, 'NVENC H.264 preflight failed'):
            require_nvenc(Mock(return_value=SimpleNamespace(returncode=1, stderr='driver mismatch')))
        require_nvenc(Mock(return_value=SimpleNamespace(returncode=0, stderr='')))

if __name__ == '__main__': unittest.main()
