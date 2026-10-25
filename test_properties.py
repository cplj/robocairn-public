"""Seeded generative properties; stdlib only, no optional test dependency.

Each test uses SEED independently. Subtest indices reproduce counterexamples;
SAMPLES controls bounded generation rather than rejection/filtering.
"""
from contextlib import redirect_stdout
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import StringIO
import json
from pathlib import Path
import random
import tempfile
import threading
import unittest

import robocairn as rc
import robocairn_app as app

SEED = 20261015
SAMPLES = 200
ROOT = Path(__file__).resolve().parent
ALPHABET = 'abcXYZ012 []{}"\\\n\t\x00é\u6a5f\u5668🤖'


def text(rng):
    return ''.join(rng.choices(ALPHABET, k=rng.randrange(40)))


def value(rng, depth=0):
    kinds = 4 if depth == 4 else 6
    kind = rng.randrange(kinds)
    if kind == 0:
        return rng.choice([None, True, False])
    if kind == 1:
        return rng.randint(-2**60, 2**60)
    if kind == 2:
        return rng.uniform(-1e100, 1e100)
    if kind == 3:
        return text(rng)
    if kind == 4:
        return [value(rng, depth + 1) for _ in range(rng.randrange(5))]
    return {text(rng): value(rng, depth + 1) for _ in range(rng.randrange(5))}


class InputProperties(unittest.TestCase):
    def test_json_roundtrip_and_canonical_idempotence(self):
        rng = random.Random(SEED)
        for index in range(SAMPLES):
            with self.subTest(seed=SEED, sample=index):
                original = value(rng)
                encoded = json.dumps(original, ensure_ascii=bool(index % 2))
                decoded = rc.loads(encoded)
                self.assertEqual(decoded, original)
                self.assertEqual(json.loads(rc.canonical(decoded)), original)
                self.assertEqual(rc.canonical(rc.loads(rc.canonical(decoded))),
                                 rc.canonical(decoded))

    def test_generated_duplicate_nonfinite_and_malformed_rejection(self):
        rng = random.Random(SEED)
        for index in range(SAMPLES):
            key = json.dumps(text(rng))
            literal = json.dumps(value(rng))
            cases = (f'{{{key}:{literal},{key}:null}}',
                     f'{{{key}:{rng.choice(["NaN", "Infinity", "-Infinity"])}}}',
                     '[' + literal + ',]')
            for body in cases:
                with self.subTest(seed=SEED, sample=index, body=body[:80]):
                    with self.assertRaises((rc.ContractError, json.JSONDecodeError)):
                        rc.loads(body)

    def test_generated_field_mutations_explain_exact_location(self):
        rng = random.Random(SEED)
        for index in range(SAMPLES):
            suite = rc.load(ROOT / 'fixtures.json')
            position = rng.randrange(len(suite['fixtures']))
            side = rng.choice(['baseline', 'candidate'])
            field = rng.choice(['outcome', 'unsafe', 'steps'])
            observation = suite['fixtures'][position][side]
            del observation[field]
            extra = 'extra_' + str(rng.getrandbits(32))
            observation[extra] = value(rng)
            with self.subTest(seed=SEED, sample=index):
                with self.assertRaises(rc.ContractError) as error:
                    app.check({'suite': suite})
                self.assertIn(f'suite.fixtures[{position}].{side}', str(error.exception))
                self.assertIn(f'missing=["{field}"]', str(error.exception))
                self.assertIn(f'unexpected=["{extra}"]', str(error.exception))

    def test_generated_limits_reject_before_comparison(self):
        rng = random.Random(SEED)
        for index in range(SAMPLES):
            kind = index % 5
            excess = rng.randint(1, 64)
            if kind == 0:
                body = '[' * (rc.MAX_DEPTH + excess) + '0' + ']' * (rc.MAX_DEPTH + excess)
            elif kind == 1:
                body = json.dumps('é' * (rc.MAX_STRING + excess))
            elif kind == 2:
                body = json.dumps([0] * (rc.MAX_ITEMS + excess))
            elif kind == 3:
                body = '{}'+ ' ' * (rc.MAX_INPUT_BYTES + excess)
            else:
                body = json.dumps([[0] * 256 for _ in range(17 + excess)])
            with self.subTest(seed=SEED, sample=index, kind=kind):
                with self.assertRaises(rc.ContractError):
                    rc.loads(body)

    def test_generated_suite_roundtrip_and_batch_permutation(self):
        rng = random.Random(SEED)
        for index in range(SAMPLES):
            suite = rc.load(ROOT / 'fixtures.json')
            for fixture in suite['fixtures']:
                fixture['seed'] = rng.randrange(2**32)
                for side in ('baseline', 'candidate'):
                    fixture[side] = dict(outcome=rng.choice(['SUCCESS', 'FAILURE', 'UNKNOWN']),
                                         unsafe=rng.choice(['TRUE', 'FALSE', 'UNKNOWN']),
                                         steps=rng.randrange(10001))
            baseline = rc.replay(suite, suite['policy']['baseline_version'])
            candidate = rc.replay(suite, suite['policy']['candidate_version'])
            with self.subTest(seed=SEED, sample=index):
                expected = app.check({'suite': suite, 'baseline': baseline, 'candidate': candidate})
                rng.shuffle(baseline['results'])
                rng.shuffle(candidate['results'])
                request = {'suite': suite, 'baseline': baseline, 'candidate': candidate}
                self.assertEqual(app.check(rc.loads(json.dumps(request))), expected)
                report = expected['report']
                self.assertEqual(sum(report['counts'].values()), len(suite['fixtures']))
                self.assertEqual(report['rows_with_unknown'], sum(
                    any(f[side][dimension] == 'UNKNOWN' for side in ('baseline', 'candidate')
                        for dimension in ('outcome', 'unsafe')) for f in suite['fixtures']))

    def test_generated_file_encoding_and_missing_argument_contract(self):
        rng = random.Random(SEED)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.json'
            for index in range(SAMPLES):
                body = b'\xff' + rng.randbytes(rng.randrange(256))
                path.write_bytes(body)
                with self.subTest(seed=SEED, sample=index), redirect_stdout(StringIO()) as output:
                    self.assertEqual(app.main(['check', str(path)]), 2)
                    envelope = json.loads(output.getvalue())
                    self.assertEqual(envelope['status'], 'ERROR')
                    self.assertEqual(envelope['error']['code'], 'INVALID_INPUT')
                    self.assertIsNone(envelope['report'])
                args = ['check', '--invalid-' + str(rng.getrandbits(32))]
                with self.subTest(seed=SEED, sample=index), redirect_stdout(StringIO()) as output:
                    with self.assertRaises(SystemExit) as error:
                        app.main(args)
                    self.assertEqual(error.exception.code, 2)
                    envelope = json.loads(output.getvalue())
                    self.assertEqual(envelope['error']['code'], 'ARGUMENT_ERROR')
                    self.assertIn('robocairn --help', envelope['error']['message'])

    def test_generated_http_invalid_input_is_error_not_unknown(self):
        rng = random.Random(SEED)
        with ThreadingHTTPServer(('127.0.0.1', 0), app.Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for index in range(SAMPLES):
                    # A valid random JSON tree wrapped in an invalid request shape,
                    # or malformed UTF-8, must never reach an internal-error envelope.
                    body = (json.dumps({'unexpected': value(rng)}).encode() if index % 2
                            else b'\xff' + rng.randbytes(rng.randrange(128)))
                    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    try:
                        connection.request('POST', '/check', body, {'Content-Type': 'application/json'})
                        response = connection.getresponse()
                        result = json.loads(response.read())
                        with self.subTest(seed=SEED, sample=index):
                            self.assertEqual(response.status, 400)
                            self.assertEqual(result['status'], 'ERROR')
                            self.assertEqual(result['error']['code'], 'INVALID_INPUT')
                            self.assertIsNone(result['report'])
                    finally:
                        connection.close()
            finally:
                server.shutdown()
                thread.join()


if __name__ == '__main__':
    print(f'Generator: random.Random; seed={SEED}; samples={SAMPLES} per property; '
          '7 properties, 2000 generated checks (duplicate/limit/transport groups included)', flush=True)
    unittest.main(verbosity=2)
