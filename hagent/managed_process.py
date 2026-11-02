"""Captured agent processes displayed live in Hagent's terminal sidebar."""
import codecs
from contextlib import contextmanager
import locale
import os
from pathlib import Path
import subprocess
import threading
import uuid

_lock = threading.Lock()
_running = {}
OUTPUT_LIMIT = 1_000_000


@contextmanager
def activity(monitor):
    """Display a persistent CLI only while it is working on a turn."""
    ident = str(uuid.uuid4())
    if monitor:
        with _lock:
            _running[ident] = {'id': ident, 'workspace_id': monitor['workspace_id'],
                'label': monitor['label'], 'run_id': monitor.get('run_id'),
                'cwd': '', 'status': 'running', 'transcript': ''}
    def append(value):
        with _lock:
            row = _running.get(ident)
            if row:
                row['transcript'] = (row['transcript'] + value)[-OUTPUT_LIMIT:]
    try:
        yield append
    finally:
        with _lock:
            _running.pop(ident, None)


def active_terminals(workspace_id):
    with _lock:
        return [dict(row) for row in _running.values() if row['workspace_id'] == workspace_id]


def _terminate_tree(process):
    import psutil
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
        psutil.wait_procs(children + [parent], timeout=3)
    except psutil.NoSuchProcess:
        pass


def run(args, *, monitor=None, **kwargs):
    """subprocess.run-compatible captured text execution for local agent adapters.

    A hidden backing console is inherited by CLI children on Windows. NO_WINDOW
    leaves the parent console-less, allowing those children to allocate visible
    consoles. All user-facing output stays in the app through redirected pipes.
    """
    kwargs.pop('capture_output', None)
    kwargs.pop('text', None)
    encoding = kwargs.pop('encoding', None) or locale.getpreferredencoding(False)
    errors = kwargs.pop('errors', 'replace')
    timeout = kwargs.pop('timeout', None)
    check = kwargs.pop('check', False)
    input_data = kwargs.pop('input', None)
    if os.name == 'nt':
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        kwargs['startupinfo'] = startup
        kwargs['creationflags'] = subprocess.CREATE_NEW_CONSOLE
    kwargs['stdin'] = subprocess.PIPE if input_data is not None else kwargs.get('stdin', subprocess.DEVNULL)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    session_id = str(uuid.uuid4())
    if monitor:
        with _lock:
            _running[session_id] = {
                'id': session_id, 'workspace_id': monitor['workspace_id'],
                'label': monitor['label'], 'run_id': monitor.get('run_id'),
                'cwd': str(kwargs.get('cwd') or Path.cwd()), 'status': 'running',
                'transcript': '',
            }
    outputs = [[], []]
    failures = []

    def read(stream, index):
        decoder = codecs.getincrementaldecoder(encoding)(errors=errors)
        try:
            while True:
                chunk = stream.read1(4096)
                text = decoder.decode(chunk, final=not chunk)
                if text:
                    outputs[index].append(text)
                    with _lock:
                        row = _running.get(session_id)
                        if row:
                            row['transcript'] = (row['transcript'] + text)[-OUTPUT_LIMIT:]
                if not chunk:
                    break
        except Exception as exc:
            failures.append(exc)
        finally:
            stream.close()

    def write():
        try:
            process.stdin.write(input_data.encode(encoding, errors))
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    threads = [threading.Thread(target=read, args=(stream, index), daemon=True)
               for index, stream in enumerate((process.stdout, process.stderr))]
    if input_data is not None:
        threads.append(threading.Thread(target=write, daemon=True))
    try:
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_tree(process)
            process.wait()
            for thread in threads:
                thread.join(timeout=3)
            raise subprocess.TimeoutExpired(args, timeout, ''.join(outputs[0]), ''.join(outputs[1]))
        for thread in threads:
            thread.join(timeout=3)
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError('An agent child kept its output pipe open after the command exited')
        if failures:
            raise failures[0]
        result = subprocess.CompletedProcess(args, process.returncode, ''.join(outputs[0]), ''.join(outputs[1]))
        if check:
            result.check_returncode()
        return result
    finally:
        if process.poll() is None:
            _terminate_tree(process)
            process.wait()
        with _lock:
            _running.pop(session_id, None)
