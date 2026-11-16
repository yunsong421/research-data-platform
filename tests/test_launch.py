import importlib.util
from pathlib import Path
import socket
import tempfile
import unittest

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LaunchAndExampleTests(unittest.TestCase):
    def test_port_in_use_chooses_next_port(self):
        launch = module_at('release_launch', ROOT / 'launch.py')
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            if port > 65514:
                self.skipTest('OS assigned a port too close to the upper limit')
            self.assertNotEqual(launch.available_port('127.0.0.1', port), port)

    def test_sample_is_reproducible_and_has_no_identity_fields(self):
        generator = module_at('sample_generator', ROOT / 'examples/generate_sample.py')
        with tempfile.TemporaryDirectory() as folder:
            generated = generator.generate(Path(folder) / 'sample.xlsx')
            actual = load_workbook(generated, data_only=True)
            shipped = load_workbook(ROOT / 'examples/sample-survey.xlsx', data_only=True)
            try:
                self.assertEqual(actual.sheetnames, ['模拟问卷', '模拟月度汇总'])
                self.assertEqual(actual.worksheets[0].max_row, 121)
                self.assertEqual(actual.worksheets[1].max_row, 7)
                for sheet, public in zip(actual.worksheets, shipped.worksheets):
                    self.assertEqual(list(sheet.values), list(public.values))
                self.assertEqual(actual.worksheets[0]['A2'].value, 'DEMO-001')
            finally:
                actual.close()
                shipped.close()


if __name__ == '__main__':
    unittest.main()
