from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ai-team-orchestrator'))
from bridge import BridgeConfig, DSHAdapter, create_handler
from dispatcher import Dispatcher
from registry import AgentRegistry
from store import TaskStore

spec = importlib.util.spec_from_file_location('team_client', ROOT / 'ai-team-supervisor/scripts/team.py')
team = importlib.util.module_from_spec(spec)
spec.loader.exec_module(team)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        workspace = root / 'workspace'
        workspace.mkdir()
        state = root / 'state'
        config = BridgeConfig(database=state/'tasks.db', state_dir=state, workspace_root=workspace,
                              command=[sys.executable, str(ROOT/'ai-team-orchestrator/fake_worker.py'), '{request_file}'])
        self.store = TaskStore(config.database)
        self.adapter = DSHAdapter(config, self.store)
        registry = AgentRegistry.default(dsh_configured=True)
        dispatcher = Dispatcher(registry=registry, adapters={'dsh': self.adapter})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(
            dispatcher=dispatcher, store=self.store, registry=registry, dsh=self.adapter, config=config))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.adapter.shutdown()
        self.store.close()

    def run_cli(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = team.main(['--bridge', self.url, *args])
        return code, json.loads(output.getvalue() or error.getvalue())

    def test_submit_handover_and_verify_actual_artifact(self):
        self.assertEqual(self.run_cli('doctor')[1]['status'], 'ok')
        self.assertTrue(self.run_cli('agents')[1]['items'])
        packet = Path(self.tmp.name)/'task.json'
        packet.write_text(json.dumps(dict(agent_id='dsh', title='offline fixture', prompt='Create artifact', workspace='.')))
        code, submitted = self.run_cli('submit', '--file', str(packet))
        self.assertEqual(code, 0)
        task_id = submitted['task_id']
        # 新客户端模拟接任主持，复用原 ID；读取不会重复派发。
        successor = team.Client(self.url)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            task = successor.request('GET', team.task_path(task_id))
            if task['status'] in {'completed', 'failed'}:
                break
            time.sleep(0.03)
        self.assertEqual(task['status'], 'completed')
        self.assertTrue(task['started'])
        self.assertTrue(task['started_at'])
        artifacts = self.run_cli('artifacts', task_id)[1]['items']
        self.assertEqual(len(artifacts), 1)
        actual = Path(self.tmp.name)/artifacts[0]['path']
        self.assertIn(task_id, actual.read_text())
        self.assertEqual(len(self.run_cli('tasks')[1]['items']), 1)
        self.assertEqual(self.run_cli('progress', task_id)[0], 0)
        self.assertEqual(self.run_cli('task', task_id)[1]['task_id'], task_id)

    def test_unknown_task_and_invalid_packet_fail_without_new_work(self):
        self.assertEqual(self.run_cli('task', 'task_missing')[0], 1)
        self.assertEqual(self.run_cli('task', '../tasks')[0], 1)
        packet = Path(self.tmp.name)/'invalid.json'
        packet.write_text('{"prompt": "not a complete task"}')
        self.assertEqual(self.run_cli('submit', '--file', str(packet))[0], 1)
        self.assertEqual(self.run_cli('tasks')[1]['items'], [])

    def test_message_and_cancel_preserve_task_truth(self):
        client = team.Client(self.url)
        task = client.request('POST', '/tasks', dict(agent_id='dsh', title='cancel fixture',
                    prompt='Wait', workspace='.', metadata={'mode': 'sleep', 'sleep_seconds': 3}))
        task_id = task['task_id']
        note = Path(self.tmp.name)/'message.txt'
        note.write_text('Keep original acceptance', encoding='utf-8')
        self.assertEqual(self.run_cli('message', task_id, '--file', str(note))[0], 0)
        self.assertEqual(self.run_cli('cancel', task_id)[1]['status'], 'cancelled')
        self.assertEqual(self.run_cli('task', task_id)[1]['status'], 'cancelled')


class BoundaryTests(unittest.TestCase):
    def test_reject_external_or_credential_urls(self):
        for url in ['https://example.com', 'http://127.0.0.1:80@evil.example',
                    'http://127.0.0.1:8765/other', 'http://localhost:8765',
                    'http://127.0.0.1:8765?token=secret']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                team.Client(url)

    def test_post_redirect_is_not_followed_and_error_body_not_echoed(self):
        seen = []
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                seen.append(self.path)
                self.send_response(307)
                self.send_header('Location', '/unexpected')
                self.end_headers()
                self.wfile.write(b'private-provider-key')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = team.Client(f'http://127.0.0.1:{server.server_port}')
            with self.assertRaisesRegex(RuntimeError, '^bridge_http_307$'):
                client.request('POST', '/tasks', {'prompt': 'private'})
            self.assertEqual(seen, ['/tasks'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
