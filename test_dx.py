"""Actionable diagnostics without changing acceptance or envelope contracts."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

import robocairn as rc
import robocairn_app as app

ROOT = Path(__file__).resolve().parent


class DiagnosticsTests(unittest.TestCase):
    def test_argument_errors_include_example_and_help(self):
        for args in ([], ['check'], ['check', '--wrong'], ['other']):
            with self.subTest(args=args), redirect_stdout(StringIO()) as output:
                with self.assertRaises(SystemExit) as error:
                    app.main(args)
                self.assertEqual(error.exception.code, 2)
                result = json.loads(output.getvalue())
                self.assertEqual(result['error']['code'], 'ARGUMENT_ERROR')
                self.assertIn('robocairn check fixtures.json', result['error']['message'])
                self.assertIn('robocairn --help', result['error']['message'])

    def test_schema_errors_name_fields_and_path(self):
        suite = rc.load(ROOT / 'fixtures.json')
        del suite['fixtures'][1]['candidate']['steps']
        suite['fixtures'][1]['candidate']['typo'] = 1
        with self.assertRaises(rc.ContractError) as error:
            rc.validate_suite(suite)
        message = str(error.exception)
        for part in ('suite.fixtures[1].candidate', 'missing=["steps"]',
                     'unexpected=["typo"]', 'docs/schemas/request.schema.json'):
            self.assertIn(part, message)

    def test_schema_wrong_type_explains_object(self):
        with self.assertRaisesRegex(rc.ContractError, 'suite: expected object'):
            rc.validate_suite([])

    def test_missing_file_explains_checkout_and_input_path(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(StringIO()) as output:
            self.assertEqual(app.main(['check', str(Path(folder) / 'absent.json')]), 2)
        message = json.loads(output.getvalue())['error']['message']
        self.assertIn('absolute path', message)
        self.assertIn('fixtures.json', message)


if __name__ == '__main__':
    unittest.main()
