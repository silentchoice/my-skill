# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML>=6.0.3,<7"]
# ///
"""Jev classifier, explainable router and optional text-model executor."""
import argparse
import json
import os
import sys
from pathlib import Path

from configuration import ConfigError, load_config, require, validate_config
from engine import ResponseError, decide, fallback
from transport import TransportError, environment, generate, post_json, target_settings, validate_endpoint

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / 'config' / 'router.yaml'


def check_endpoints(config):
    validate_endpoint(config['jev']['endpoint'])
    for model in config['models'].values():
        if 'endpoint' in model:
            validate_endpoint(model['endpoint'])


def route(config, state, supplied_response=None, request_id=None):
    validate_config(config)
    check_endpoints(config)
    if supplied_response is not None:
        result = decide(config, supplied_response, request_id)
        result['source'] = 'fixture'
    else:
        require(isinstance(state, (str, list, dict)) and bool(state), 'Nonempty text/object/array state is required')
        jev = config['jev']
        try:
            response = post_json(jev['endpoint'], {'model': jev['model'], 'state': state, 'questions': config['scenarios']},
                                 jev['api_key_env'], jev.get('timeout_seconds', 10))
            result = decide(config, response, request_id)
            result['source'] = 'live'
        except (TransportError, ResponseError) as exc:
            if config['routing'].get('on_jev_error', 'fallback') == 'stop':
                raise
            result = fallback(config, 'jev_error', source='live_error', error=str(exc))
    model = config['models'][result['selected']]
    result['model'] = model.get('model') or os.environ.get(model.get('model_env', '')) or None
    result['mode'] = config['routing']['mode']
    return result


def execute(config, decision, state):
    validate_config(config)
    require(decision.get('source') in {'live', 'live_error'}, 'Fixture decisions cannot execute real model calls')
    require(decision.get('selected') in config['models'], 'Decision selected an unknown model')
    return {**decision, 'execution': generate(config['models'][decision['selected']], state)}


def read_state(args):
    if args.input is not None:
        require(bool(args.input.strip()), 'Input must not be empty')
        return args.input
    if args.input_file:
        path = Path(args.input_file)
        content = path.read_text(encoding='utf-8-sig')
        state = json.loads(content) if path.suffix.lower() == '.json' else content
        require(isinstance(state, (str, list, dict)) and bool(state), 'Input must be nonempty text, JSON object or array')
        return state
    raise ConfigError('Provide --input-file or --input for live classification')


def emit(value, stream=sys.stdout):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), file=stream)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('validate', 'decide', 'execute'):
        command = commands.add_parser(name)
        command.add_argument('--config', default=str(DEFAULT_CONFIG))
        if name == 'validate':
            command.add_argument('--check-execution', action='store_true', help='Check env vars and endpoints without a network call')
        else:
            group = command.add_mutually_exclusive_group()
            group.add_argument('--input-file')
            group.add_argument('--input')
            command.add_argument('--request-id', help='Stable traffic bucket; omit for random allocation')
            if name == 'decide':
                command.add_argument('--response-file', help='Offline Jev response JSON; makes no API calls')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        check_endpoints(config)
        if args.command == 'validate':
            if args.check_execution:
                environment(config['jev']['api_key_env'])
                for model in config['models'].values():
                    target_settings(model)
            emit({'valid': True, 'scenarios': list(config['scenarios']), 'models': list(config['models']),
                  'execution_settings_checked': args.check_execution})
            return 0
        response_file = getattr(args, 'response_file', None)
        supplied = json.loads(Path(response_file).read_text(encoding='utf-8-sig')) if response_file else None
        if response_file:
            require(isinstance(supplied, dict), 'Fixture must be a JSON object')
        state = None if response_file else read_state(args)
        result = route(config, state, supplied, args.request_id)
        if args.command == 'execute':
            try:
                result = execute(config, result, state)
            except (TransportError, ConfigError) as exc:
                emit({**result, 'execution': {'status': 'failed', 'error': str(exc)}})
                return 3
        emit(result)
        return 0
    except (ConfigError, ResponseError, TransportError) as exc:
        emit({'error': str(exc), 'type': type(exc).__name__}, sys.stderr)
        return 2
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        emit({'error': 'Cannot read input/configuration; check file path, UTF-8, JSON and nesting', 'type': 'InputError'}, sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
