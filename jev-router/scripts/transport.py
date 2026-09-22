"""Bounded JSON HTTP calls; secrets and response bodies never enter error messages."""
import json
import os
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from configuration import ConfigError, require, text


class TransportError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(NoRedirect())
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def validate_endpoint(endpoint):
    require(text(endpoint), 'Endpoint is required')
    try:
        parts = urlsplit(endpoint)
        _ = parts.port  # Force validation of malformed ports.
    except ValueError as exc:
        raise ConfigError('Invalid endpoint URL') from exc
    require(parts.scheme == 'https' or (parts.scheme == 'http' and parts.hostname in {'localhost', '127.0.0.1', '::1'}),
            'Endpoints require HTTPS (HTTP allowed only for localhost)')
    require(bool(parts.hostname) and not parts.username and not parts.password and not parts.fragment and not parts.query,
            'Endpoint must not contain credentials, query parameters or fragments')
    return endpoint


def environment(name):
    value = os.environ.get(name, '')
    if not value.strip():
        raise TransportError(f'Missing environment variable: {name}')
    return value


def post_json(endpoint, payload, api_key_env=None, timeout=10):
    validate_endpoint(endpoint)
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if api_key_env:
        token = environment(api_key_env)
        if '\n' in token or '\r' in token:
            raise TransportError('Invalid API key environment value')
        headers['Authorization'] = 'Bearer ' + token
    try:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        request = Request(endpoint, data=data, headers=headers, method='POST')
        with OPENER.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TransportError('Response exceeded 8 MiB')
        result = json.loads(raw.decode('utf-8'))
        if not isinstance(result, dict):
            raise TransportError('Expected a JSON object response')
        return result
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise TransportError(f'HTTP {code}; response body omitted') from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise TransportError('Network connection or timeout failure') from None
    except (UnicodeError, ValueError):
        raise TransportError('Invalid JSON request or response') from None


def target_settings(model):
    model_id = model.get('model') or environment(model['model_env'])
    endpoint = model.get('endpoint')
    if endpoint is None and model.get('endpoint_env'):
        endpoint = environment(model['endpoint_env'])
    validate_endpoint(endpoint)
    if model.get('api_key_env'):
        environment(model['api_key_env'])
    return model_id, endpoint


def generate(model, state):
    model_id, endpoint = target_settings(model)
    content = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, allow_nan=False)
    payload = {**model.get('parameters', {}), 'model': model_id,
               'messages': [{'role': 'user', 'content': content}], 'stream': False}
    response = post_json(endpoint, payload, model.get('api_key_env'), model.get('timeout_seconds', 120))
    choices = response.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise TransportError('Model response is missing choices')
    choice = choices[0]
    message = choice.get('message')
    if not isinstance(message, dict) or not isinstance(message.get('content'), str):
        raise TransportError('Model response is not text; this executor does not run tools')
    if choice.get('finish_reason') != 'stop':
        raise TransportError('Model response was incomplete, filtered or requested tools')
    return {'status': 'completed', 'model': model_id, 'text': message['content']}
