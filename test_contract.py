import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import robocairn as rc

ROOT = Path(__file__).parent


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.suite = rc.load(ROOT / 'fixtures.json')
        self.baseline = rc.replay(self.suite, 'baseline-v1')
        self.candidate = rc.replay(self.suite, 'candidate-v1')

    def reject_result(self, change):
        change(self.candidate['results'])
        with self.assertRaises(rc.ContractError):
            rc.compare(self.suite, self.baseline, self.candidate)

    def test_predetermined_oracle(self):
        oracle = rc.load(ROOT / 'oracle.json')
        report = rc.compare(self.suite, self.baseline, self.candidate)
        self.assertEqual(report['counts'], oracle['counts'])
        for actual, expected in zip(report['rows'], oracle['rows'], strict=True):
            self.assertEqual(actual['id'], expected['id'])
            self.assertEqual(actual['seed'], expected['seed'])
            self.assertEqual(actual['classification'], expected['classification'])
            for side in ('baseline', 'candidate'):
                self.assertEqual({k: actual[side][k] for k in ('outcome', 'unsafe', 'steps')},
                                 expected[side])

    def test_deterministic_replay_bytes(self):
        for version in ('baseline-v1', 'candidate-v1'):
            first = subprocess.check_output([sys.executable, str(ROOT / 'robocairn.py'),
                                             'replay', str(ROOT / 'fixtures.json'), version])
            second = subprocess.check_output([sys.executable, str(ROOT / 'robocairn.py'),
                                              'replay', str(ROOT / 'fixtures.json'), version])
            self.assertEqual(first, second)
            self.assertEqual(json.loads(first), rc.replay(self.suite, version))

    def test_order_independent_pairing(self):
        expected = rc.compare(self.suite, self.baseline, self.candidate)
        self.candidate['results'].reverse()
        self.assertEqual(rc.compare(self.suite, self.baseline, self.candidate), expected)

    def test_missing_result(self):
        self.reject_result(lambda rows: rows.pop())

    def test_duplicate_result(self):
        self.reject_result(lambda rows: rows.append(copy.deepcopy(rows[0])))

    def test_unexpected_result(self):
        self.reject_result(lambda rows: rows[0].update(id='not-in-suite'))

    def test_missing_and_extra_fields(self):
        for field in ('id', 'seed', 'outcome', 'unsafe', 'steps'):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.candidate)
                del candidate['results'][0][field]
                with self.assertRaises(rc.ContractError):
                    rc.compare(self.suite, self.baseline, candidate)
        self.reject_result(lambda rows: rows[0].update(extra=1))

    def test_invalid_values_and_ranges(self):
        for field, values in {
            'outcome': ['PASS', None, True, 1, [], {}],
            'unsafe': [False, None, 'false', 0, [], {}],
            'steps': [-1, 10001, 1.5, True, None, '10', float('nan'), float('inf')],
            'seed': [-1, 2**32, 42002, True, 42001.0, None],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    candidate = copy.deepcopy(self.candidate)
                    candidate['results'][0][field] = value
                    with self.assertRaises(rc.ContractError):
                        rc.compare(self.suite, self.baseline, candidate)

    def test_result_range_boundaries(self):
        self.candidate['results'][0]['steps'] = 0
        self.candidate['results'][1]['steps'] = 10000
        rc.compare(self.suite, self.baseline, self.candidate)

    def test_scope_drift(self):
        mutations = [lambda x: x['scope']['environment'].update(version='2'),
                     lambda x: x['scope']['source'].update(version='2'),
                     lambda x: x['scope'].update(policy_id='other'),
                     lambda x: x['scope'].update(suite_sha256='0' * 64),
                     lambda x: x['scope'].update(schema_version=True),
                     lambda x: x.update(policy_version='baseline-v1')]
        for mutate in mutations:
            candidate = copy.deepcopy(self.candidate)
            mutate(candidate)
            with self.assertRaises(rc.ContractError):
                rc.compare(self.suite, self.baseline, candidate)

    def test_tape_or_seed_drift_rejects_old_results(self):
        for mutation in (lambda s: s['fixtures'][0].update(seed=1),
                         lambda s: s['fixtures'][0]['candidate'].update(outcome='FAILURE')):
            suite = copy.deepcopy(self.suite)
            mutation(suite)
            with self.assertRaises(rc.ContractError):
                rc.compare(suite, self.baseline, self.candidate)

    def test_unknown_retained_and_not_success(self):
        rows = rc.compare(self.suite, self.baseline, self.candidate)['rows']
        self.assertEqual(rows[3]['candidate']['outcome'], 'UNKNOWN')
        self.assertEqual(rows[4]['candidate']['unsafe'], 'UNKNOWN')
        self.assertEqual(rows[10]['classification'], 'UNKNOWN')
        self.assertEqual(rows[11]['classification'], 'REGRESSION')
        self.assertEqual(rows[11]['unknown_dimensions'], ['unsafe'])
        self.assertEqual(sum(bool(r['unknown_dimensions']) for r in rows), 5)

    def test_regression_takes_precedence_over_improvement(self):
        self.baseline['results'][0].update(outcome='FAILURE', unsafe='FALSE')
        self.candidate['results'][0].update(outcome='SUCCESS', unsafe='TRUE')
        row = rc.compare(self.suite, self.baseline, self.candidate)['rows'][0]
        self.assertEqual(row['classification'], 'REGRESSION')

    def test_suite_invalid(self):
        mutations = [lambda s: s.update(schema_version=True),
                     lambda s: s['policy'].update(candidate_version='baseline-v1'),
                     lambda s: s['source'].update(evidence='SIMULATOR'),
                     lambda s: s.update(fixtures=s['fixtures'][:9]),
                     lambda s: s['fixtures'][0].update(seed=-1),
                     lambda s: s['fixtures'][1].update(id='case-01')]
        for mutate in mutations:
            suite = copy.deepcopy(self.suite)
            mutate(suite)
            with self.assertRaises(rc.ContractError):
                rc.replay(suite, 'baseline-v1')

    def test_unknown_policy(self):
        with self.assertRaises(rc.ContractError):
            rc.replay(self.suite, 'unregistered')

    def test_owned_replay_values(self):
        self.baseline['scope']['environment']['version'] = 'edited'
        self.assertEqual(self.suite['environment']['version'], '1')

    def test_duplicate_json_keys_and_nonfinite_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.json'
            for text in ('{"a":1,"a":2}', '{"nested":{"a":1,"a":2}}',
                         '{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}'):
                path.write_text(text)
                with self.assertRaises(rc.ContractError):
                    rc.load(path)

    def test_nonfinite_scope_raises_contract_error(self):
        for side in ('baseline', 'candidate'):
            for value in (float('inf'), float('-inf'), float('nan'),
                          {'nested': [float('inf')]}, [float('-inf')]):
                for field in ('schema_version', 'environment', 'source',
                              'suite_sha256', 'policy_id'):
                    with self.subTest(side=side, field=field, value=value):
                        baseline = copy.deepcopy(self.baseline)
                        candidate = copy.deepcopy(self.candidate)
                        batch = baseline if side == 'baseline' else candidate
                        batch['scope'][field] = value
                        with self.assertRaises(rc.ContractError):
                            rc.compare(self.suite, baseline, candidate)

    def test_json_overflow_scope_sdk_and_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            baseline = Path(folder) / 'baseline.json'
            candidate = Path(folder) / 'candidate.json'
            baseline.write_text(rc.canonical(self.baseline))
            for literal in ('1e999', '-1e999', '{"nested":[1e999]}'):
                with self.subTest(literal=literal):
                    text = rc.canonical(self.candidate).replace(
                        '"schema_version":1', '"schema_version":' + literal)
                    candidate.write_text(text)
                    with self.assertRaises(rc.ContractError):
                        rc.compare(self.suite, self.baseline, rc.load(candidate))
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'robocairn.py'), 'compare',
                         str(ROOT / 'fixtures.json'), str(baseline), str(candidate)],
                        capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('REJECTED:', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)

    def test_cli_comparison_and_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / name for name in ('baseline.json', 'candidate.json')]
            for path, obj in zip(paths, (self.baseline, self.candidate)):
                path.write_text(rc.canonical(obj))
            command = [sys.executable, str(ROOT / 'robocairn.py'), 'compare',
                       str(ROOT / 'fixtures.json'), *map(str, paths)]
            valid = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertEqual(json.loads(valid.stdout)['rows_with_unknown'], 5)
            paths[1].write_text('{"missing":"results"}')
            invalid = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(invalid.stdout, '')
            self.assertIn('REJECTED:', invalid.stderr)


if __name__ == '__main__':
    unittest.main()
