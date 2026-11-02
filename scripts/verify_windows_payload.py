"""Exercise a staged Windows package with fresh data and a real terminal session."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload', type=Path)
    args = parser.parse_args()
    payload = args.payload.resolve(strict=True)
    root = Path(__file__).resolve().parents[1]
    data = Path(tempfile.mkdtemp(prefix='packaged-acceptance-', dir=root / 'output'))
    report = {'payload': str(payload), 'data': str(data), 'pages': {}}
    env = dict(os.environ, HAGENT_DESKTOP_DATA=str(data))
    with (data / 'server.log').open('wb') as log:
        process = subprocess.Popen([str(payload / 'runtime/python.exe'), '-m', 'hagent.desktop'],
                                   stdin=subprocess.PIPE, stdout=log, stderr=log, env=env,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 30
            base = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Packaged server exited; inspect ' + str(data / 'server.log'))
                try:
                    port = json.loads((data / 'desktop-endpoint.json').read_text())['port']
                    base = f'http://127.0.0.1:{port}'
                    with urllib.request.urlopen(base + '/healthz', timeout=1) as response:
                        if response.status == 200:
                            break
                except (OSError, ValueError):
                    time.sleep(.1)
            else:
                raise RuntimeError('Packaged server did not start')
            for path in ['/', '/board', '/agents', '/connections', '/terminal', '/settings',
                         '/api/terminal/sessions', '/api/terminal/activity']:
                with urllib.request.urlopen(base + path, timeout=10) as response:
                    assert response.status == 200, path
                    assert '/login' not in response.url, path
                    report['pages'][path] = response.status
            with connect(base.replace('http:', 'ws:') + '/ws/terminal?cwd=' + str(data),
                         origin=base, open_timeout=10, close_timeout=5) as ws:
                ready = json.loads(ws.recv(timeout=15))
                assert ready['type'] == 'ready', ready
                ws.send(json.dumps({'type': 'input', 'data': "Write-Output ('HAGENT_PACKAGED_' + (6 * 7))\r"}))
                output = ''
                deadline = time.monotonic() + 20
                while 'HAGENT_PACKAGED_42' not in output:
                    if time.monotonic() > deadline:
                        raise RuntimeError('Packaged terminal did not return result')
                    event = json.loads(ws.recv(timeout=20))
                    assert event['type'] != 'error', event
                    output += event.get('data', '')
                ws.send(json.dumps({'type': 'stop'}))
                try:
                    while True:
                        ws.recv(timeout=10)
                except ConnectionClosed:
                    pass
            with urllib.request.urlopen(base + '/api/terminal/sessions/' + ready['id']) as response:
                saved = json.load(response)
                assert saved['status'] == 'closed', saved
                assert 'HAGENT_PACKAGED_42' in saved['transcript']
            report['terminal'] = 'real command, stop, and persisted transcript passed'
        finally:
            if process.poll() is None:
                process.stdin.write(b'stop\n')
                process.stdin.flush()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    raise RuntimeError('Packaged server failed graceful shutdown')
        assert process.returncode == 0, process.returncode
        report['shutdown'] = 'passed'
    destination = root / 'output/packaged-acceptance.json'
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
