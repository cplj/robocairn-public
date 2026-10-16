"""Strict, deterministic replay of manual fixtures. No simulator or robot SDK."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

MAX_INPUT_BYTES = 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 4096
MAX_ITEMS = 256
MAX_STRING = 1024


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def input_limits(value):
    """Bound SDK objects before recursion, canonicalization or batch iteration."""
    pending = [(value, 1)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        require(nodes <= MAX_NODES, f'input exceeds {MAX_NODES} JSON nodes')
        if type(item) in (dict, list):
            require(depth <= MAX_DEPTH, f'input exceeds nesting depth {MAX_DEPTH}')
            require(len(item) <= MAX_ITEMS, f'input collection exceeds {MAX_ITEMS} items')
            if type(item) is dict:
                for key, child in item.items():
                    require(type(key) is str, 'JSON keys must be strings')
                    pending.extend(((key, depth + 1), (child, depth + 1)))
            else:
                pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            require(len(item) <= MAX_STRING, f'input string exceeds {MAX_STRING} characters')


def keys(value, expected, label):
    schema = 'docs/schemas/request.schema.json'
    require(type(value) is dict, f'{label}: expected object; schema: {schema}')
    missing = sorted(set(expected.split()) - set(value))
    unexpected = sorted(set(value) - set(expected.split()))
    require(not missing and not unexpected,
            f'{label}: missing={canonical(missing)}; unexpected={canonical(unexpected)}; '
            f'schema: {schema}')


def nonempty(value):
    return type(value) is str and bool(value.strip())


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def observation(value, label='observation'):
    keys(value, 'outcome unsafe steps', label)
    require(value['outcome'] in ('SUCCESS', 'FAILURE', 'UNKNOWN'), 'invalid outcome')
    require(value['unsafe'] in ('TRUE', 'FALSE', 'UNKNOWN'), 'invalid unsafe status')
    require(integer(value['steps'], 0, 10000), 'steps outside [0,10000]')


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ContractError(f'invalid canonical JSON: {error}') from error


def fingerprint(suite):
    return hashlib.sha256(canonical(suite).encode()).hexdigest()


def validate_suite(suite):
    input_limits(suite)
    keys(suite, 'schema_version environment policy source fixtures', 'suite')
    require(type(suite['schema_version']) is int and suite['schema_version'] == 1,
            'unsupported schema version')
    for name in ('environment', 'source'):
        expected = 'id version' if name == 'environment' else 'id version evidence'
        keys(suite[name], expected, f'suite.{name}')
        require(all(nonempty(v) for v in suite[name].values()), f'invalid {name}')
    require(suite['source']['evidence'] == 'SCHEMA_REPLAY_CONTRACT_ONLY',
            'unsupported evidence level')
    keys(suite['policy'], 'id baseline_version candidate_version', 'suite.policy')
    require(all(nonempty(v) for v in suite['policy'].values()), 'invalid policy')
    require(suite['policy']['baseline_version'] != suite['policy']['candidate_version'],
            'policy versions must differ')
    require(type(suite['fixtures']) is list and 10 <= len(suite['fixtures']) <= 20,
            'suite needs 10 to 20 fixtures')
    seen = set()
    for index, fixture in enumerate(suite['fixtures']):
        label = f'suite.fixtures[{index}]'
        keys(fixture, 'id variant seed baseline candidate', label)
        require(nonempty(fixture['id']) and nonempty(fixture['variant']), 'invalid fixture identity')
        require(fixture['id'] not in seen, 'duplicate fixture id')
        seen.add(fixture['id'])
        require(integer(fixture['seed'], 0, 2**32 - 1), 'seed outside uint32')
        observation(fixture['baseline'], f'{label}.baseline')
        observation(fixture['candidate'], f'{label}.candidate')
    return suite


def scope(suite):
    return {name: suite[name] for name in ('schema_version', 'environment', 'source')} | {
        'suite_sha256': fingerprint(suite), 'policy_id': suite['policy']['id']}


def replay(suite, policy_version):
    """Replay a fixed tape, without executing a policy or consuming randomness."""
    validate_suite(suite)
    policy = suite['policy']
    require(policy_version in (policy['baseline_version'], policy['candidate_version']),
            'unknown policy version')
    tape = 'baseline' if policy_version == policy['baseline_version'] else 'candidate'
    batch = {'scope': scope(suite), 'policy_version': policy_version, 'results': [
        {'id': f['id'], 'seed': f['seed'], **f[tape]} for f in suite['fixtures']]}
    # Return owned JSON values so callers cannot mutate the suite via result aliases.
    return json.loads(canonical(batch))


def validate_batch(suite, batch, policy_version):
    validate_suite(suite)
    input_limits(batch)
    keys(batch, 'scope policy_version results', 'batch')
    # Canonical comparison distinguishes JSON true from numeric 1.
    require(canonical(batch['scope']) == canonical(scope(suite)), 'scope mismatch')
    require(batch['policy_version'] == policy_version, 'policy version mismatch')
    require(type(batch['results']) is list, 'results must be a list')
    require(len(batch['results']) == len(suite['fixtures']),
            'results count must match the 10 to 20 fixtures')
    expected = {f['id']: f for f in suite['fixtures']}
    seen = {}
    for index, result in enumerate(batch['results']):
        keys(result, 'id seed outcome unsafe steps', f'batch.results[{index}]')
        require(nonempty(result['id']), 'invalid result id')
        require(result['id'] in expected, 'unexpected result id')
        require(result['id'] not in seen, 'duplicate result id')
        require(integer(result['seed'], 0, 2**32 - 1), 'invalid result seed')
        require(result['seed'] == expected[result['id']]['seed'], 'seed mismatch')
        observation({k: result[k] for k in ('outcome', 'unsafe', 'steps')})
        seen[result['id']] = result
    require(set(seen) == set(expected), 'missing results')
    return seen


def compare(suite, baseline, candidate):
    """Compare paired observations only within the exact declared fixture scope."""
    validate_suite(suite)
    before = validate_batch(suite, baseline, suite['policy']['baseline_version'])
    after = validate_batch(suite, candidate, suite['policy']['candidate_version'])
    rows = []
    for fixture in suite['fixtures']:
        case_id = fixture['id']
        old, new = before[case_id], after[case_id]
        unknown = [k for k in ('outcome', 'unsafe') if 'UNKNOWN' in (old[k], new[k])]
        regressed = (old['outcome'], new['outcome']) == ('SUCCESS', 'FAILURE') or (
            old['unsafe'], new['unsafe']) == ('FALSE', 'TRUE')
        improved = (old['outcome'], new['outcome']) == ('FAILURE', 'SUCCESS') or (
            old['unsafe'], new['unsafe']) == ('TRUE', 'FALSE')
        if regressed:
            classification = 'REGRESSION'
        elif unknown:
            classification = 'UNKNOWN'
        elif improved:
            classification = 'IMPROVEMENT'
        elif new['unsafe'] == 'TRUE':
            classification = 'UNCHANGED_UNSAFE'
        elif new['outcome'] == 'FAILURE':
            classification = 'UNCHANGED_FAILURE'
        else:
            classification = 'UNCHANGED'
        rows.append({'id': case_id, 'seed': fixture['seed'], 'baseline': old,
                     'candidate': new, 'classification': classification,
                     'unknown_dimensions': unknown})
    return {'scope': scope(suite), 'baseline_policy_version': baseline['policy_version'],
            'candidate_policy_version': candidate['policy_version'],
            'evidence': 'SCHEMA_REPLAY_CONTRACT_ONLY', 'rows': rows,
            'counts': dict(sorted(Counter(r['classification'] for r in rows).items())),
            'rows_with_unknown': sum(bool(r['unknown_dimensions']) for r in rows)}


def loads(text):
    require(len(text) <= MAX_INPUT_BYTES and len(text.encode('utf-8')) <= MAX_INPUT_BYTES,
            f'input exceeds {MAX_INPUT_BYTES} bytes')
    # Inspect nesting before the JSON decoder allocates a recursive container tree.
    depth = 0
    quoted = escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in '[{':
            depth += 1
            require(depth <= MAX_DEPTH, f'input exceeds nesting depth {MAX_DEPTH}')
        elif character in ']}':
            depth -= 1

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f'duplicate JSON key: {key}')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ContractError(f'nonfinite JSON number: {value}')

    value = json.loads(text, object_pairs_hook=unique_pairs,
                       parse_constant=invalid_constant)
    input_limits(value)
    return value


def load(path):
    with Path(path).open('rb') as source:
        body = source.read(MAX_INPUT_BYTES + 1)
    require(len(body) <= MAX_INPUT_BYTES, f'input exceeds {MAX_INPUT_BYTES} bytes')
    return loads(body.decode('utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    replay_parser = sub.add_parser('replay')
    replay_parser.add_argument('suite')
    replay_parser.add_argument('policy_version')
    compare_parser = sub.add_parser('compare')
    compare_parser.add_argument('suite')
    compare_parser.add_argument('baseline')
    compare_parser.add_argument('candidate')
    args = parser.parse_args(argv)
    try:
        suite = load(args.suite)
        if args.command == 'replay':
            result = replay(suite, args.policy_version)
        else:
            result = compare(suite, load(args.baseline), load(args.candidate))
        print(canonical(result))
        return 0
    except (ContractError, OSError, json.JSONDecodeError, ValueError, TypeError) as error:
        print(f'REJECTED: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
