"""Installable CLI and loopback HTTP API over the existing replay contract."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys

import robocairn as rc

VERSION = '0.1.0'
MAX_BODY = rc.MAX_INPUT_BYTES
INPUT_ERRORS = (rc.ContractError, ValueError, TypeError, RecursionError)


def failure(status, code, message):
    return {'status': status, 'report': None, 'error': {'code': code, 'message': message}}


def check(request):
    """Compare supplied batches, or replay both declared versions if omitted."""
    rc.input_limits(request)
    rc.require(type(request) is dict, 'request must be an object')
    rc.require(set(request) in ({'suite'}, {'suite', 'baseline', 'candidate'}),
               'request needs suite and optionally both baseline and candidate')
    suite = rc.validate_suite(request['suite'])
    if 'baseline' in request:
        baseline, candidate = request['baseline'], request['candidate']
    else:
        baseline = rc.replay(suite, suite['policy']['baseline_version'])
        candidate = rc.replay(suite, suite['policy']['candidate_version'])
    report = rc.compare(suite, baseline, candidate)
    status = 'UNKNOWN' if report['rows_with_unknown'] else 'OK'
    return {'status': status, 'report': report, 'error': None}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def respond(self, status_code, result):
        body = rc.canonical(result).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def send_error(self, code, message=None, explain=None):
        self.respond(code, failure('ERROR', 'HTTP_ERROR', message or self.responses[code][0]))

    def do_POST(self):
        if self.path != '/check':
            self.respond(404, failure('ERROR', 'NOT_FOUND', 'Use POST /check'))
            return
        if self.headers.get_content_type() != 'application/json':
            self.respond(415, failure('ERROR', 'CONTENT_TYPE', 'Expected application/json'))
            return
        lengths = self.headers.get_all('Content-Length', [])
        if (self.headers.get('Transfer-Encoding') is not None or len(lengths) != 1
                or not lengths[0].isascii() or not lengths[0].isdecimal()):
            self.respond(400, failure('ERROR', 'BODY_LENGTH', 'One Content-Length is required; no transfer encoding'))
            return
        # Compare length text before int conversion to reject arbitrarily large integers.
        length_text = lengths[0].lstrip('0') or '0'
        if len(length_text) > len(str(MAX_BODY)) or int(length_text) > MAX_BODY:
            self.respond(413, failure('ERROR', 'BODY_TOO_LARGE', f'Maximum body is {MAX_BODY} bytes'))
            return
        length = int(length_text)
        try:
            body = self.rfile.read(length)
            rc.require(len(body) == length, 'incomplete request body')
            result = check(rc.loads(body.decode('utf-8')))
        except (OSError, *INPUT_ERRORS) as error:
            self.respond(400, failure('ERROR', 'INVALID_INPUT', str(error)))
            return
        except Exception:
            self.respond(500, failure('UNKNOWN', 'INTERNAL_ERROR', 'Comparison could not be completed'))
            return
        self.respond(200, result)

    def do_GET(self):
        self.respond(405, failure('ERROR', 'METHOD_NOT_ALLOWED', 'Use POST /check'))

    def log_message(self, format, *args):
        # Do not write request bodies or caller-controlled paths to logs.
        pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        message += '; Example: robocairn check fixtures.json; Help: robocairn --help'
        print(rc.canonical(failure('ERROR', 'ARGUMENT_ERROR', message)))
        raise SystemExit(2)


def main(argv=None):
    parser = Parser(prog='robocairn', description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('check', help='Compare a fixture suite or supplied observation batches')
    run.add_argument('suite', help='Path to suite JSON')
    run.add_argument('--baseline', help='Path to baseline batch JSON (requires --candidate)')
    run.add_argument('--candidate', help='Path to candidate batch JSON (requires --baseline)')
    serve = commands.add_parser('serve', help='Serve POST /check (loopback by default)')
    serve.add_argument('--host', choices=('127.0.0.1', '0.0.0.0'), default='127.0.0.1',
                       help='Bind address; use 0.0.0.0 inside a container')
    serve.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if args.command == 'serve':
        if not 1 <= args.port <= 65535:
            parser.error('port must be in [1,65535]')
        try:
            with ThreadingHTTPServer((args.host, args.port), Handler) as server:
                print(f'RoboCairn {VERSION}: http://{args.host}:{server.server_port}/check',
                      file=sys.stderr, flush=True)
                server.serve_forever()
        except KeyboardInterrupt:
            return 0
        except OSError as error:
            print(rc.canonical(failure('ERROR', 'SERVER_ERROR', str(error))))
            return 2
        return 0
    try:
        rc.require(bool(args.baseline) == bool(args.candidate),
                   '--baseline and --candidate must be supplied together')
        request = {'suite': rc.load(args.suite)}
        if args.baseline:
            request.update(baseline=rc.load(args.baseline), candidate=rc.load(args.candidate))
        result = check(request)
    except FileNotFoundError as error:
        result = failure('ERROR', 'INVALID_INPUT',
                         f'{error}; use an existing absolute path or run '
                         'robocairn check fixtures.json from the repository root; '
                         'Help: robocairn check --help')
    except (OSError, *INPUT_ERRORS) as error:
        result = failure('ERROR', 'INVALID_INPUT', str(error))
    except Exception:
        result = failure('UNKNOWN', 'INTERNAL_ERROR', 'Comparison could not be completed')
    print(rc.canonical(result))
    return {'OK': 0, 'ERROR': 2, 'UNKNOWN': 3}[result['status']]


if __name__ == '__main__':
    sys.exit(main())
