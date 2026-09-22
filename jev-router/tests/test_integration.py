"""A local HTTP server verifies wire contracts without paid API calls."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from test_router import SCRIPTS, config, response


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.available = importlib.util.find_spec('router') is not None
        if cls.available:
            import router
            import transport
            from configuration import ConfigError, load_config
            cls.router, cls.transport = router, transport
            cls.ConfigError = ConfigError
            cls.load_config = staticmethod(load_config)

    def setUp(self):
        self.assertTrue(self.available, 'HTTP/CLI implementation does not exist yet')
        self.calls = []
        self.jev_status, self.model_status = 200, 200
        self.jev_body = response()
        self.model_body = {'model': 'fixture-strong', 'choices': [{'message': {'role': 'assistant', 'content': '模型回答：你好'}, 'finish_reason': 'stop'}]}
        self.redirect = False
        self.truncate = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.calls.append((self.path, payload, self.headers.get('Authorization')))
                if owner.truncate:
                    self.send_response(200)
                    self.send_header('Transfer-Encoding', 'chunked')
                    self.end_headers()
                    self.wfile.write(b'20\r\n{"answers":')
                    self.close_connection = True
                    return
                if owner.redirect:
                    self.send_response(307)
                    self.send_header('Location', owner.endpoint + '/leak')
                    self.end_headers()
                    return
                body = owner.jev_body if self.path == '/jev' else owner.model_body
                status = owner.jev_status if self.path == '/jev' else owner.model_status
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8'))

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.cfg = config()
        self.cfg['jev']['endpoint'] = self.endpoint + '/jev'
        for model in self.cfg['models'].values():
            model.update(endpoint=self.endpoint + '/chat', api_key_env='TEST_MODEL_KEY')
        self.env_patch = patch.dict(os.environ, {'TYPESAFE_API_KEY': 'fake-jev-secret', 'TEST_MODEL_KEY': 'fake-model-secret'})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_jev_batches_all_questions_and_execute_calls_selected_model(self):
        state = {'task': '排查并发错误', 'context': ['必要上下文']}
        decision = self.router.route(self.cfg, state)
        self.assertEqual(decision['source'], 'live')
        result = self.router.execute(self.cfg, decision, state)
        self.assertEqual(result['execution']['text'], '模型回答：你好')
        self.assertEqual(self.calls[0], ('/jev', {'model': 'jev-latest', 'state': state, 'questions': self.cfg['scenarios']}, 'Bearer fake-jev-secret'))
        path, body, auth = self.calls[1]
        self.assertEqual((path, body['model'], auth), ('/chat', 'strong', 'Bearer fake-model-secret'))
        self.assertEqual(json.loads(body['messages'][0]['content']), state)
        self.assertEqual(body['stream'], False)
        self.assertNotIn('fake-', json.dumps(result))

    def test_http_errors_and_bad_responses_fallback_with_reason(self):
        for status in (401, 429, 500):
            with self.subTest(status=status):
                self.jev_status = status
                self.jev_body = {'error': 'do not echo fake-jev-secret'}
                decision = self.router.route(self.cfg, 'task')
                self.assertEqual((decision['selected'], decision['reason']), ('strong', 'jev_error'))
                self.assertNotIn('fake-jev-secret', json.dumps(decision))
        self.jev_status = 200
        for bad in (b'not-json', {}, {'answers': {}}, {'answers': {'coding': {'noul': .8}}}):
            self.jev_body = bad
            self.assertEqual(self.router.route(self.cfg, 'task')['reason'], 'jev_error')

    def test_stop_policy_and_missing_key_do_not_invent_decisions(self):
        self.cfg['routing']['on_jev_error'] = 'stop'
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': ''}):
            with self.assertRaises(self.transport.TransportError):
                self.router.route(self.cfg, 'task')
        self.assertEqual(self.calls, [])

    def test_timeout_is_classification_failure(self):
        with patch('transport.OPENER.open', side_effect=TimeoutError()):
            result = self.router.route(self.cfg, 'task')
        self.assertEqual(result['reason'], 'jev_error')

    def test_truncated_http_body_uses_fallback(self):
        self.truncate = True
        result = self.router.route(self.cfg, 'task')
        self.assertEqual(result['reason'], 'jev_error')

    def test_oversized_json_integer_is_rejected_as_invalid_response(self):
        self.jev_body['answers']['coding']['noul'] = 10**400
        self.assertEqual(self.router.route(self.cfg, 'task')['reason'], 'jev_error')

    def test_redirect_never_forwards_bearer(self):
        self.redirect = True
        result = self.router.route(self.cfg, 'task')
        self.assertEqual(result['reason'], 'jev_error')
        self.assertEqual(len(self.calls), 1)

    def test_execution_failure_is_not_reported_as_success_or_retried(self):
        decision = self.router.route(self.cfg, 'task')
        self.model_status = 500
        with self.assertRaises(self.transport.TransportError):
            self.router.execute(self.cfg, decision, 'task')
        self.assertEqual(len(self.calls), 2)

    def test_simulated_result_cannot_execute(self):
        decision = self.router.route(self.cfg, 'task', supplied_response=response())
        self.assertEqual(decision['source'], 'fixture')
        with self.assertRaises(self.ConfigError):
            self.router.execute(self.cfg, decision, 'task')
        self.assertEqual(self.calls, [])

    def test_invalid_config_and_endpoint_fail_without_network(self):
        self.cfg['routing']['rules'][0]['target'] = 'missing'
        with self.assertRaises(self.ConfigError):
            self.router.route(self.cfg, 'task')
        self.assertEqual(self.calls, [])
        for endpoint in ('http://example.com/api', 'https://user:password@example.com/api', 'file:///x'):
            with self.assertRaises(self.ConfigError):
                self.transport.validate_endpoint(endpoint)

    def test_yaml_duplicate_keys_and_noul_boolean_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.yaml'
            for content in ('version: 1\nversion: 2\n', 'scenarios:\n  coding:\n    criteria:\n      true: yes\n'):
                path.write_text(content, encoding='utf-8')
                with self.assertRaises(self.ConfigError):
                    self.load_config(path)

    def test_non_json_yaml_values_fail_config_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.yaml'
            import yaml
            content = yaml.safe_dump(self.cfg)
            content = content.replace('Requires code changes?', '2026-09-22')
            path.write_text(content, encoding='utf-8')
            with self.assertRaises(self.ConfigError):
                self.load_config(path)
            # A valid outer parameter mapping can still contain a YAML date.
            from datetime import date
            self.cfg['models']['strong']['parameters'] = {'metadata': {'date': date(2026, 9, 22)}}
            with self.assertRaises(self.ConfigError):
                self.router.route(self.cfg, 'task')

    def test_cli_validate_offline_and_live_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cfg_path, res_path, task_path = base/'router.json', base/'response.json', base/'task.txt'
            cfg_path.write_text(json.dumps(self.cfg), encoding='utf-8')
            res_path.write_text(json.dumps(response()), encoding='utf-8')
            task_path.write_text('请排查这个并发错误', encoding='utf-8')

            def run(*args):
                return subprocess.run([sys.executable, str(SCRIPTS/'router.py'), *args, '--config', str(cfg_path)], capture_output=True, text=True, encoding='utf-8')

            result = run('validate')
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run('decide', '--response-file', str(res_path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['source'], 'fixture')
            self.assertEqual(self.calls, [])
            result = run('execute', '--input-file', str(task_path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['execution']['status'], 'completed')
            self.assertEqual(len(self.calls), 2)
            self.model_status = 500
            result = run('execute', '--input-file', str(task_path))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)['execution']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
