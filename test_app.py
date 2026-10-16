import copy
from contextlib import redirect_stdout
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import robocairn as rc
import robocairn_app as app

ROOT = Path(__file__).resolve().parent


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.suite = rc.load(ROOT / 'fixtures.json')

    def request(self, body, method='POST', path='/check', headers=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            conn.request(method, path, body, headers or {'Content-Type': 'application/json'})
            response = conn.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.getheader('Content-Type'), 'application/json; charset=utf-8')
            return response.status, result
        finally:
            conn.close()

    def known_suite(self):
        suite = copy.deepcopy(self.suite)
        for fixture in suite['fixtures']:
            for side in ('baseline', 'candidate'):
                fixture[side] = {'outcome': 'SUCCESS', 'unsafe': 'FALSE', 'steps': 10}
        return suite

    def test_unknown_preserves_known_regressions(self):
        result = app.check({'suite': self.suite})
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['report']['counts'], rc.load(ROOT / 'oracle.json')['counts'])
        self.assertEqual(result['report']['rows_with_unknown'], 5)
        self.assertEqual(result['report']['evidence'], 'SCHEMA_REPLAY_CONTRACT_ONLY')
        self.assertIsNone(result['error'])

    def test_normal_http_and_function_agree(self):
        request = {'suite': self.known_suite()}
        code, result = self.request(rc.canonical(request))
        self.assertEqual(code, 200)
        self.assertEqual(result, app.check(request))
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['report']['counts'], {'UNCHANGED': 12})

    def test_supplied_batches_are_compared_not_replayed(self):
        suite = self.known_suite()
        baseline = rc.replay(suite, 'baseline-v1')
        candidate = rc.replay(suite, 'candidate-v1')
        candidate['results'][0]['outcome'] = 'FAILURE'
        code, result = self.request(rc.canonical(dict(suite=suite, baseline=baseline, candidate=candidate)))
        self.assertEqual(code, 200)
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['report']['counts']['REGRESSION'], 1)

    def test_http_unknown(self):
        code, result = self.request(rc.canonical({'suite': self.suite}))
        self.assertEqual(code, 200)
        self.assertEqual(result, app.check({'suite': self.suite}))

    def test_invalid_request_shapes(self):
        for value in (None, [], {}, {'suite': self.suite, 'baseline': {}},
                      {'suite': self.suite, 'extra': 1}, {'suite': None}):
            with self.subTest(value_type=type(value).__name__):
                code, result = self.request(rc.canonical(value))
                self.assertEqual(code, 400)
                self.assertEqual(result['status'], 'ERROR')
                self.assertIsNone(result['report'])

    def test_bad_json_and_encoding(self):
        for body in (b'{', b'\xff', b'{"suite":null,"suite":null}',
                     b'{"suite":NaN}', b'[' * 1500 + b']' * 1500):
            with self.subTest(body=body[:30]):
                code, result = self.request(body)
                self.assertEqual(code, 400)
                self.assertEqual(result['error']['code'], 'INVALID_INPUT')

    def test_scope_mismatch_is_error(self):
        request = dict(suite=self.suite, baseline=rc.replay(self.suite, 'baseline-v1'),
                       candidate=rc.replay(self.suite, 'candidate-v1'))
        request['candidate']['scope']['environment']['version'] = 'drift'
        code, result = self.request(rc.canonical(request))
        self.assertEqual(code, 400)
        self.assertIn('scope mismatch', result['error']['message'])

    def test_http_routes_and_content_type(self):
        for method, path, headers, expected in (
                ('GET', '/check', None, 405), ('POST', '/wrong', None, 404),
                ('POST', '/check', {'Content-Type': 'text/plain'}, 415),
                ('PUT', '/check', None, 501)):
            code, result = self.request('{}', method, path, headers)
            self.assertEqual(code, expected)
            self.assertEqual(result['status'], 'ERROR')

    def test_body_limit_boundary(self):
        body = rc.canonical({'suite': self.known_suite()}).encode()
        code, result = self.request(body + b' ' * (app.MAX_BODY - len(body)))
        self.assertEqual(code, 200)
        self.assertEqual(result['status'], 'OK')
        # Oversize requests are rejected from headers before reading any body.
        code, result = self.request(b'', headers={
            'Content-Type': 'application/json', 'Content-Length': str(app.MAX_BODY + 1)})
        self.assertEqual(code, 413)
        self.assertEqual(result['error']['code'], 'BODY_TOO_LARGE')

    def test_http_framing_errors(self):
        for lengths, extra, expected in (([], {}, 400), (['-1'], {}, 400),
                                         (['1', '1'], {}, 400), (['9' * 5000], {}, 413),
                                         (['0'], {'Transfer-Encoding': 'chunked'}, 400)):
            conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
            try:
                conn.putrequest('POST', '/check')
                conn.putheader('Content-Type', 'application/json')
                for length in lengths:
                    conn.putheader('Content-Length', length)
                for key, value in extra.items():
                    conn.putheader(key, value)
                conn.endheaders()
                response = conn.getresponse()
                self.assertEqual(response.status, expected)
                self.assertEqual(json.loads(response.read())['status'], 'ERROR')
            finally:
                conn.close()

    def test_unexpected_failure_is_unknown(self):
        with patch.object(app, 'check', side_effect=RuntimeError('internal detail')):
            code, result = self.request(rc.canonical({'suite': self.suite}))
        self.assertEqual(code, 500)
        self.assertEqual(result, app.failure('UNKNOWN', 'INTERNAL_ERROR', 'Comparison could not be completed'))

    def test_suite_size_seed_and_steps_boundaries(self):
        for size in (10, 20):
            suite = self.known_suite()
            suite['fixtures'] = [copy.deepcopy(suite['fixtures'][0]) for _ in range(size)]
            for index, fixture in enumerate(suite['fixtures']):
                fixture.update(id=f'boundary-{index}', seed=0 if index == 0 else 2**32 - 1)
                fixture['baseline']['steps'] = 0
                fixture['candidate']['steps'] = 10000
            self.assertEqual(app.check({'suite': suite})['status'], 'OK')
        for size in (9, 21):
            suite['fixtures'] = [copy.deepcopy(suite['fixtures'][0]) for _ in range(size)]
            with self.assertRaises(rc.ContractError):
                app.check({'suite': suite})

    def cli(self, *args):
        # A different cwd ensures installed modules and the entry point work outside product/.
        with tempfile.TemporaryDirectory() as folder:
            return subprocess.run(['robocairn', *map(str, args)], cwd=folder,
                                  capture_output=True, text=True, timeout=15)

    def test_installed_cli_unknown_and_version(self):
        result = self.cli('check', ROOT / 'fixtures.json')
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout), app.check({'suite': self.suite}))
        self.assertEqual(self.cli('--version').stdout.strip(), '0.1.0')
        for args in (('--help',), ('check', '--help'), ('serve', '--help')):
            self.assertEqual(self.cli(*args).returncode, 0)

    def test_installed_cli_normal_and_explicit_batches(self):
        with tempfile.TemporaryDirectory() as folder:
            suite = self.known_suite()
            paths = [Path(folder) / name for name in ('suite.json', 'baseline.json', 'candidate.json')]
            values = [suite, rc.replay(suite, 'baseline-v1'), rc.replay(suite, 'candidate-v1')]
            values[2]['results'][0]['outcome'] = 'FAILURE'
            for path, value in zip(paths, values):
                path.write_text(rc.canonical(value), encoding='utf-8')
            result = self.cli('check', paths[0], '--baseline', paths[1], '--candidate', paths[2])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['report']['counts']['REGRESSION'], 1)

    def test_installed_cli_errors(self):
        for args in (('check', ROOT / 'absent.json'),
                     ('check', ROOT / 'fixtures.json', '--baseline', 'only-one.json'),
                     ('serve', '--port', '0'), ('serve', '--port', '65536'),
                     ('serve', '--port', 'not-a-number'), ('check',), ('unsupported',)):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'], 'ERROR')
                self.assertNotIn('Traceback', result.stderr)

    def test_installed_cli_invalid_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.json'
            for value in ('{', '{"a":1,"a":2}', '{"a":NaN}'):
                path.write_text(value, encoding='utf-8')
                result = self.cli('check', path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)['status'], 'ERROR')

    def test_server_binding_for_local_and_container_use(self):
        for arguments, expected_host in (([], '127.0.0.1'),
                                          (['--host', '0.0.0.0'], '0.0.0.0')):
            with self.subTest(arguments=arguments), patch.object(app, 'ThreadingHTTPServer') as server:
                server.return_value.__enter__.return_value.server_port = 8765
                with patch('sys.stderr', new=StringIO()):
                    self.assertEqual(app.main(['serve', *arguments]), 0)
                server.assert_called_once_with((expected_host, 8765), app.Handler)

    def test_server_rejects_unsupported_bind_host(self):
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as error:
            app.main(['serve', '--host', '192.0.2.1'])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'ERROR')


if __name__ == '__main__':
    unittest.main()
