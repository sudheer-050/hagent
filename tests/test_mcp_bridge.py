"""Exercise the actual CLI child process, not a mocked SDK constructor."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
from contextvars import ContextVar
import urllib.error
import urllib.request

from hagent.mcp_bridge import serve_tools


def test_bridge_stdio_lists_calls_and_reports_errors():
    context = ContextVar('test_run_context', default=None)
    context.set('parent-run')
    def execute(name, arguments):
        assert context.get() == 'parent-run'
        if name == 'fail':
            raise ValueError('deliberate test failure')
        return 'Hello ' + arguments['name']

    schema = {'type': 'object', 'properties': {'name': {'type': 'string'}}}
    tools = [{'name': name, 'input_schema': schema} for name in ('hello', 'fail')]
    with serve_tools(tools, execute) as url:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).parents[1] / 'hagent/mcp_bridge_stub.py'), url],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        replies = queue.Queue()
        def read():
            for line in process.stdout:
                replies.put(json.loads(line))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()

        def send(method, params=None, ident=None):
            message = {'jsonrpc': '2.0', 'method': method}
            if params is not None:
                message['params'] = params
            if ident is not None:
                message['id'] = ident
            process.stdin.write(json.dumps(message) + '\n')
            process.stdin.flush()
            if ident is not None:
                result = replies.get(timeout=20)
                assert result['id'] == ident
                return result['result']
        try:
            send('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                               'clientInfo': {'name': 'test', 'version': '1'}}, 1)
            send('notifications/initialized')
            listed = send('tools/list', {}, 2)
            assert listed['tools'][0]['inputSchema'] == schema
            called = send('tools/call', {'name': 'hello', 'arguments': {'name': 'Holly'}}, 3)
            assert called['content'][0]['text'] == 'Hello Holly'
            failed = send('tools/call', {'name': 'fail', 'arguments': {}}, 4)
            assert failed['isError'] is True
            assert 'deliberate test failure' in failed['content'][0]['text']
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=5)


def test_bridge_refuses_requests_without_token_or_with_browser_origin():
    import pytest
    with serve_tools([], lambda *_: None) as url:
        for address, headers in [(url.rsplit('/', 1)[0] + '/tools', {}),
                                 (url + '/tools', {'Origin': 'https://example.com'})]:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(address, headers=headers), timeout=5)
            assert error.value.code == 404
