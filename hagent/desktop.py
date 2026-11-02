"""Packaged Windows entry point; all mutable data lives outside the install folder."""
import json
import os
from pathlib import Path
import socket
import sys
import threading


def configure_data():
    root = Path(os.environ.get('HAGENT_DESKTOP_DATA',
                str(Path(os.environ['LOCALAPPDATA']) / 'HagentTest'))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for variable, filename in [('HAGENT_DB_PATH', 'hagent.db'),
                               ('HAGENT_CONFIG_PATH', 'config.json'),
                               ('HAGENT_REMOTE_PATH', 'remote.json')]:
        os.environ[variable] = str(root / filename)
    os.chdir(root)
    return root


def main():
    root = configure_data()
    import uvicorn
    from hagent.web import app
    # Reserve the socket before announcing it; never attach to an unrelated app.
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))

    def stop_on_eof():
        sys.stdin.readline()
        server.should_exit = True
    threading.Thread(target=stop_on_eof, daemon=True).start()
    (root / 'desktop-endpoint.json').write_text(json.dumps({'port': port}), encoding='utf-8')
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()
        (root / 'desktop-endpoint.json').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
