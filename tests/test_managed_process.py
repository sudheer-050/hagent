import concurrent.futures
import ctypes
import json
import os
import subprocess
import sys
import time

import pytest

from hagent.managed_process import active_terminals, run


def test_live_output_is_scoped_and_removed_when_done():
    monitor = {'workspace_id': 'test-live', 'label': 'Test agent'}
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = pool.submit(run, [sys.executable, '-u', '-c',
            "import time; print('live output', flush=True); time.sleep(2); print('finished')"],
            monitor=monitor, capture_output=True, text=True, timeout=10)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            rows = active_terminals('test-live')
            if rows and 'live output' in rows[0]['transcript']:
                break
            time.sleep(.02)
        else:
            pytest.fail('No live output before process completion')
        assert not result.done()
        assert not active_terminals('other-workspace')
        completed = result.result(timeout=10)
    assert completed.returncode == 0
    assert 'finished' in completed.stdout
    assert not active_terminals('test-live')


@pytest.mark.skipif(os.name != 'nt', reason='Windows console inheritance')
def test_nested_cli_console_is_hidden_and_inherited():
    inspect = ('import ctypes,json; k=ctypes.windll.kernel32; '
               'k.GetConsoleWindow.restype=ctypes.c_void_p; h=k.GetConsoleWindow(); '
               'print(json.dumps([h,bool(ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(h)))]))')
    script = inspect + '; import subprocess,sys; subprocess.run([sys.executable,"-c",' + repr(inspect) + '])'
    result = run([sys.executable, '-u', '-c', script],
                 monitor={'workspace_id': 'test-console', 'label': 'Nested CLI'},
                 capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    parent, child = [json.loads(line) for line in result.stdout.splitlines()]
    assert parent[0] and parent == child
    assert parent[1] is False


@pytest.mark.parametrize('monitor', [None, {'workspace_id': 'test-timeout', 'label': 'Timeout'}])
def test_timeout_kills_child_and_removes_tab(monitor):
    import psutil
    code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(p.pid,flush=True); time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired) as error:
        run([sys.executable, '-u', '-c', code],
            monitor=monitor,
            capture_output=True, text=True, timeout=1)
    assert not psutil.pid_exists(int(error.value.output.strip()))
    assert not active_terminals('test-timeout')


def test_stdin_and_stderr_remain_separate():
    result = run([sys.executable, '-c', "import sys; print(sys.stdin.read()); print('warning',file=sys.stderr)"],
                 monitor={'workspace_id': 'test-input', 'label': 'Input'},
                 input='hello', capture_output=True, text=True, timeout=5)
    assert result.stdout.strip() == 'hello'
    assert result.stderr.strip() == 'warning'
