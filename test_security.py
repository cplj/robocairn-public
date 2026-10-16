"""Resource, network and distribution regression checks."""
import ast
import base64
import copy
import csv
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
from pathlib import Path
import re
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import robocairn as rc
import robocairn_app as app

ROOT = Path(__file__).resolve().parent


class ResourceTests(unittest.TestCase):
    def test_json_byte_limit_inclusive_and_multibyte(self):
        self.assertEqual(rc.loads('{}' + ' ' * (1048576 - 2)), {})
        for text in ('{}' + ' ' * (1048576 - 1), '"' + 'é' * 524288 + '"'):
            with self.subTest(size=len(text)), self.assertRaisesRegex(rc.ContractError, 'bytes'):
                rc.loads(text)

    def test_bounded_file_read_and_cli_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'large.json'
            path.write_bytes(b'{}' + b' ' * (1048576 - 1))
            with self.assertRaisesRegex(rc.ContractError, 'bytes'):
                rc.load(path)
            output = StringIO()
            with patch('sys.stdout', output):
                self.assertEqual(app.main(['check', str(path)]), 2)
            result = json.loads(output.getvalue())
            self.assertEqual(result['status'], 'ERROR')
            self.assertEqual(result['error']['code'], 'INVALID_INPUT')
            self.assertIn('bytes', result['error']['message'])
            with patch.object(Path, 'open') as source:
                source.return_value.__enter__.return_value.read.return_value = b'{}'
                rc.load(path)
                source.return_value.__enter__.return_value.read.assert_called_once_with(1048577)

    def test_depth_rejected_before_decoder(self):
        with patch.object(rc.json, 'loads', side_effect=AssertionError('decoder reached')):
            with self.assertRaisesRegex(rc.ContractError, 'nesting depth'):
                rc.loads('[' * 33 + '0' + ']' * 33)
        self.assertIsInstance(rc.loads('[' * 32 + '0' + ']' * 32), list)
        self.assertEqual(rc.loads(json.dumps('brackets [ { and escaped " \\ ')),
                         'brackets [ { and escaped " \\ ')

    def test_string_collection_and_node_limits(self):
        for value, message in ((['x' * 1025], 'string'), ([0] * 257, 'collection'),
                               ({str(i): 0 for i in range(257)}, 'collection'),
                               ([[0] * 255 for _ in range(17)], 'nodes')):
            with self.subTest(message=message):
                with self.assertRaisesRegex(rc.ContractError, message):
                    rc.input_limits(value)
                with self.assertRaisesRegex(rc.ContractError, message):
                    rc.loads(json.dumps(value))
        rc.input_limits(['x' * 1024, [0] * 256])
        rc.input_limits([[0] * 254 for _ in range(16)] + [0] * 15)  # 4096 nodes
        with self.assertRaisesRegex(rc.ContractError, 'nodes'):
            rc.input_limits([[0] * 254 for _ in range(16)] + [0] * 16)

    def test_sdk_depth_and_cycles_are_contract_errors(self):
        value = 0
        for _ in range(33):
            value = [value]
        cycle = []
        cycle.append(cycle)
        for request in (value, cycle):
            with self.assertRaisesRegex(rc.ContractError, 'nesting depth'):
                app.check(request)

    def test_sdk_suite_and_batch_limits(self):
        suite = rc.load(ROOT / 'fixtures.json')
        suite['environment']['id'] = 'x' * 1024
        rc.validate_suite(suite)
        suite['environment']['id'] += 'x'
        for call in (lambda: rc.replay(suite, 'baseline-v1'),
                     lambda: app.check({'suite': suite})):
            with self.assertRaisesRegex(rc.ContractError, 'string'):
                call()
        suite = rc.load(ROOT / 'fixtures.json')
        baseline = rc.replay(suite, 'baseline-v1')
        candidate = rc.replay(suite, 'candidate-v1')
        for size in (0, 21, 256):
            candidate['results'] = [copy.deepcopy(baseline['results'][0]) for _ in range(size)]
            with self.subTest(size=size), self.assertRaisesRegex(rc.ContractError, 'results count'):
                rc.compare(suite, baseline, candidate)
        candidate = rc.replay(suite, 'candidate-v1')
        candidate['scope']['policy_id'] = 'x' * 1025
        with self.assertRaisesRegex(rc.ContractError, 'string'):
            rc.compare(suite, baseline, candidate)

    def test_http_excess_input_is_error_and_server_survives(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            suite = rc.load(ROOT / 'fixtures.json')
            suite['environment']['id'] = 'x' * 1025
            for body in (json.dumps({'suite': suite}), '[' * 33 + '0' + ']' * 33,
                         json.dumps([0] * 257)):
                conn = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    conn.request('POST', '/check', body, {'Content-Type': 'application/json'})
                    response = conn.getresponse()
                    result = json.loads(response.read())
                    self.assertEqual(response.status, 400)
                    self.assertEqual(result['status'], 'ERROR')
                    self.assertEqual(result['error']['code'], 'INVALID_INPUT')
                    self.assertIsNone(result['report'])
                finally:
                    conn.close()
            self.assertEqual(app.check({'suite': rc.load(ROOT / 'fixtures.json')})['status'], 'UNKNOWN')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class NetworkTests(unittest.TestCase):
    def test_runtime_has_no_outbound_client_or_connection_sink(self):
        allowed = {'argparse', 'collections', 'hashlib', 'json', 'pathlib', 'sys',
                   'http.server', 'robocairn'}
        for name in ('robocairn.py', 'robocairn_app.py'):
            tree = ast.parse((ROOT / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertTrue({alias.name for alias in node.names} <= allowed)
                elif isinstance(node, ast.ImportFrom):
                    self.assertIn(node.module, allowed)
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        self.assertNotIn(node.func.id, ('__import__', 'eval', 'exec'))
                    elif isinstance(node.func, ast.Attribute):
                        self.assertNotIn(node.func.attr, ('connect', 'create_connection', 'urlopen',
                                                         'request', 'getaddrinfo', 'gethostbyname'))

    def test_private_link_local_metadata_and_other_hosts_never_resolved(self):
        urls = ('http://127.0.0.1/', 'http://10.0.0.1/', 'http://172.16.0.1/',
                'http://192.168.1.1/', 'http://169.254.169.254/latest/meta-data/',
                'http://[::1]/', 'http://[fe80::1]/', 'http://[fd00::1]/',
                'http://metadata.google.internal/', 'https://example.com/redirect')
        with patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')) as dns, \
             patch.object(socket.socket, 'connect', side_effect=AssertionError('outbound forbidden')) as connect:
            for url in urls:
                with self.subTest(url=url):
                    with self.assertRaises(rc.ContractError):
                        app.check({'suite': url})
                    with self.assertRaises(rc.ContractError):
                        app.check({'suite': rc.load(ROOT / 'fixtures.json'), 'url': url})
                    suite = rc.load(ROOT / 'fixtures.json')
                    suite['environment']['id'] = url
                    self.assertEqual(app.check({'suite': suite})['status'], 'UNKNOWN')
            dns.assert_not_called()
            connect.assert_not_called()

    def test_fixed_loopback_test_client_does_not_follow_redirects(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', 'http://169.254.169.254/latest/meta-data/')
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        conn = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            conn.connect()
            with patch.object(socket.socket, 'connect', side_effect=AssertionError('redirect followed')):
                conn.request('GET', '/')
                response = conn.getresponse()
                self.assertEqual(response.status, 302)
                self.assertIn('169.254.169.254', response.getheader('Location'))
                response.read()
        finally:
            conn.close()
            server.shutdown()
            server.server_close()
            thread.join()


class DistributionTests(unittest.TestCase):
    def test_readme_and_wheel_have_no_internal_identity_paths(self):
        patterns = (rb'/[Uu]sers/', rb'Doing\s+Nothing', rb'\d{2}_Company', rb'\d{2}_QA')
        with zipfile.ZipFile(ROOT / 'wheels/robocairn-0.1.0-py3-none-any.whl') as wheel:
            contents = [(name, wheel.read(name)) for name in wheel.namelist()]
        contents.append(('README.md', (ROOT / 'README.md').read_bytes()))
        for name, data in contents:
            for pattern in patterns:
                self.assertIsNone(re.search(pattern, data), name)

    def test_wheel_source_metadata_and_record_match(self):
        with zipfile.ZipFile(ROOT / 'wheels/robocairn-0.1.0-py3-none-any.whl') as wheel:
            self.assertIsNone(wheel.testzip())
            for name in ('robocairn.py', 'robocairn_app.py'):
                self.assertEqual(wheel.read(name), (ROOT / name).read_bytes())
            info = 'robocairn-0.1.0.dist-info/'
            self.assertTrue(wheel.read(info + 'METADATA').endswith((ROOT / 'README.md').read_bytes()))
            records = list(csv.reader(StringIO(wheel.read(info + 'RECORD').decode())))
            self.assertEqual({row[0] for row in records}, set(wheel.namelist()))
            for name, digest, size in records:
                if name.endswith('/RECORD'):
                    continue
                data = wheel.read(name)
                expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
                self.assertEqual(digest, 'sha256=' + expected)
                self.assertEqual(int(size), len(data))

    def test_image_is_nonroot_and_immutable(self):
        text = (ROOT / 'Dockerfile').read_text()
        self.assertRegex(text, r'(?m)^FROM python:\d+\.\d+\.\d+-slim@sha256:[0-9a-f]{64}$')
        self.assertRegex(text, r'(?m)^USER 65532:65532$')
        self.assertNotRegex(text, r'(?m)^ADD ')

    def test_all_workflow_actions_are_full_commit_pins(self):
        files = list((ROOT / 'ci').glob('*.yml'))
        files += list((ROOT / '_pending_workflows').glob('*.yml'))
        files += list((ROOT / '.github/workflows').glob('*.yml'))
        self.assertTrue(files)
        for path in files:
            refs = re.findall(r'uses:\s*([^\s#]+)', path.read_text())
            self.assertTrue(refs, path)
            for ref in refs:
                self.assertRegex(ref, r'^[\w/-]+@[0-9a-f]{40}$', str(path))
        if (ROOT / '_pending_workflows').exists():
            self.assertFalse(list((ROOT / '.github/workflows').glob('*')))


if __name__ == '__main__':
    unittest.main()
