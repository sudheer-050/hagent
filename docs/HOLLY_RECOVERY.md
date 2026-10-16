# Holly Voice Recovery Manual

## Scope

Use this guide when the **Holly Voice** desktop shortcut does nothing, opens
a blank window, starts several Python processes, cannot speak, or never reaches
the listening screen. The recovery steps preserve Holly's history, memory, and
application data.

Commands below use PowerShell. Set these two paths first for the affected
computer:

```powershell
$HollyRoot = 'C:\Users\YOUR-NAME\Documents\projects\ollama-personal-model'
$HollyPython = 'C:\Users\YOUR-NAME\AppData\Local\Programs\Python\Python310\python.exe'
Set-Location -LiteralPath $HollyRoot
```

Do not copy the example username literally. If Python is installed elsewhere,
use the interpreter that has Holly's dependencies installed.

## Quick recovery

1. Confirm Docker Desktop is running.
2. Start the Kokoro container if port `8881` is not listening:

   ```powershell
   docker start holly-kokoro-tts
   ```

3. Launch Holly from the project folder:

   ```powershell
   & '.\launch_holly_voice.bat'
   ```

4. Allow up to two minutes for the speech models to load after a cold start.
5. Complete every check in [Verify the recovery](#verify-the-recovery).

The launcher prevents duplicate servers while the models load. It first tries
the frameless WebView2 widget. If WebView2 fails, it automatically opens Holly
as an Edge or Chrome app window.

## Diagnose the failure

Read the two runtime logs first:

```powershell
Get-Content "$env:LOCALAPPDATA\Holly\holly_server.log" -Tail 100
Get-Content "$env:LOCALAPPDATA\Holly\holly_widget.log" -Tail 100
```

Run the widget with visible error output when the shortcut appears to do
nothing:

```powershell
& $HollyPython '.\holly_widget.py'
```

Check Holly's ports:

```powershell
netstat -ano | findstr /C:":5000 " /C:":8881 " /C:":47653 " /C:":47654 "
```

Port meanings:

| Port | Expected owner | Purpose |
| --- | --- | --- |
| `5000` | `pythonw.exe` | Holly HTTP server |
| `8881` | Docker | Kokoro text-to-speech |
| `47653` | `pythonw.exe` | Widget single-instance lock |
| `47654` | `pythonw.exe` | Server single-instance lock |

During model loading, `47654` may appear before `5000`. That is normal and
means another shortcut click will not start a second model server.

## Remove duplicate Holly servers safely

Only use this section when more than one PID is listening on port `5000`.
First list the exact listeners:

```powershell
$HollyServerPids = @(
    netstat -ano |
        Select-String ':5000\s+.*LISTENING' |
        ForEach-Object { [int](($_.Line -split '\s+')[-1]) } |
        Sort-Object -Unique
)
$HollyServerPids
Get-Process -Id $HollyServerPids | Select-Object Id, ProcessName, StartTime, Path
```

Verify that every listed process is Holly's Python server. Then stop only those
verified PIDs:

```powershell
$HollyServerPids | ForEach-Object { Stop-Process -Id $_ -Force }
```

After all verified Holly servers have stopped, remove the generated PID file:

```powershell
Remove-Item -LiteralPath "$env:TEMP\holly_server.lock" -Force -ErrorAction SilentlyContinue
```

Never use `taskkill /f /im pythonw.exe`; it can also terminate unrelated Python
applications and Holly's reminder daemon.

## Recover the server

Check that the required files and Python interpreter exist:

```powershell
Test-Path -LiteralPath $HollyPython
Test-Path -LiteralPath (Join-Path $HollyRoot 'holly_server.py')
Test-Path -LiteralPath (Join-Path $HollyRoot 'start_holly_server.ps1')
```

Start the normal launcher rather than starting several copies manually:

```powershell
& '.\launch_holly_voice.bat'
```

If the server still fails, run it visibly:

```powershell
& $HollyPython '.\holly_server.py'
```

The first process binds port `47654` before loading the large speech models.
Any second server process should detect that lock and exit immediately.

## Recover the voice window

If `holly_widget.log` contains `WebView2 initialization failed`, the launcher
will wait ten seconds and then open the same local Holly page in Edge or Chrome
app mode. If neither window opens:

1. Confirm Microsoft Edge or Google Chrome is installed.
2. Repair or install the Microsoft Edge WebView2 Runtime.
3. Confirm `http://127.0.0.1:5000/` opens in a browser.
4. Run `holly_widget.py` with `python.exe` to expose the error instead of hiding
   it behind `pythonw.exe`.

Holly stores WebView and fallback-browser data under:

```text
%LOCALAPPDATA%\Holly\WebView2
%LOCALAPPDATA%\Holly\BrowserProfile
```

Do not delete these folders during ordinary recovery. Rename one only when its
log clearly indicates profile corruption and Holly is fully stopped; renaming
keeps the old profile recoverable.

## Recover Kokoro TTS

Check Docker and the container:

```powershell
docker info
docker ps -a --filter 'name=holly-kokoro-tts'
docker logs --tail 100 holly-kokoro-tts
```

Start the existing container:

```powershell
docker start holly-kokoro-tts
```

Port `8881` must listen before voice output can work:

```powershell
netstat -ano | findstr /C:":8881 "
```

If the locally patched GPU image or container was removed, follow the rebuild
instructions in `HOLLY_SETUP.md`. Do not replace it with an arbitrary image on
systems that require the CUDA 12.8/Blackwell fix.

## Verify the recovery

Run every check below before considering recovery complete.

### 1. Run the automated smoke check

```powershell
& $HollyPython '.\test_holly_startup.py'
```

Expected result:

```text
Holly server smoke check passed
```

### 2. Verify the HTTP page

```powershell
$HollyResponse = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:5000/' -TimeoutSec 5
$HollyResponse.StatusCode
([regex]::Match($HollyResponse.Content, '<title>(.*?)</title>', 'IgnoreCase')).Groups[1].Value
```

Expected results are status `200` and title `Holly`.

### 3. Verify one server and both server ports

```powershell
$HollyListeners = netstat -ano | Select-String ':5000\s+.*LISTENING|:47654\s+.*LISTENING'
$HollyListeners
$HollyListenerPids = @(
    $HollyListeners |
        ForEach-Object { [int](($_.Line -split '\s+')[-1]) } |
        Sort-Object -Unique
)
"Holly server PID count: $($HollyListenerPids.Count)"
```

Expected: ports `5000` and `47654` use the same PID, and the PID count is `1`.

### 4. Verify Kokoro TTS

```powershell
netstat -ano | findstr /C:":8881 "
docker ps --filter 'name=holly-kokoro-tts' --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Expected: port `8881` is listening and the container status begins with `Up`.

### 5. Verify the desktop shortcut target

```powershell
$HollyShortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Holly Voice.lnk'
$HollyShell = New-Object -ComObject WScript.Shell
$HollyShortcut = $HollyShell.CreateShortcut($HollyShortcutPath)
$HollyShortcut.TargetPath
$HollyShortcut.WorkingDirectory
```

Expected: the target is `launch_holly_voice.bat` and the working directory is
the Holly project folder. On OneDrive-managed desktops, use the actual shortcut
path under `OneDrive\Desktop` if `GetFolderPath('Desktop')` does not find it.

### 6. Verify duplicate-launch protection

With Holly already running, double-click **Holly Voice** twice. Then repeat the
PID-count command from step 3. The count must remain `1`.

### 7. Verify the rendered interface

Confirm the visible page shows:

- `HOLLY`
- `Listening…`
- text-input, microphone, speaker, and end-conversation controls

Test one short spoken request and confirm that Holly transcribes it and produces
audible speech. If the browser asks for microphone access, allow it only for
the local Holly page.

### 8. Check the final logs

```powershell
Get-Content "$env:LOCALAPPDATA\Holly\holly_server.log" -Tail 40
Get-Content "$env:LOCALAPPDATA\Holly\holly_widget.log" -Tail 40
```

The server log should show Flask listening on `127.0.0.1:5000`. A WebView2
error is acceptable only when the next widget-log entry says the Edge or Chrome
fallback opened and the Holly interface is visibly working.

## Logs and runtime files

| Path | Purpose |
| --- | --- |
| `%LOCALAPPDATA%\Holly\holly_server.log` | Server startup and Python errors |
| `%LOCALAPPDATA%\Holly\holly_widget.log` | WebView2 and browser-fallback errors |
| `%LOCALAPPDATA%\Holly\holly_widget.ready` | Exists only while a verified widget or fallback is active |
| `%TEMP%\holly_server.lock` | PID of the server started by the launcher |
| `%LOCALAPPDATA%\Holly\WebView2` | Persistent WebView2 profile |
| `%LOCALAPPDATA%\Holly\BrowserProfile` | Edge/Chrome fallback profile |

Holly conversation history and memory files are stored in the project folder.
The recovery steps above do not remove them.

## Common symptoms

| Symptom | Likely cause | Recovery |
| --- | --- | --- |
| Shortcut appears to do nothing | `pythonw.exe` hid a widget error | Read `holly_widget.log`; run the widget with `python.exe` |
| Blank Holly window | WebView2 failed to initialize | Wait for Edge/Chrome fallback; repair WebView2 if needed |
| Several Python processes consume memory or GPU | Repeated launches occurred before port `5000` was ready | Stop only verified port-5000 PIDs, then relaunch once |
| Server takes a long time to appear | Speech models are loading | Confirm port `47654`, then wait up to two minutes |
| Holly listens but cannot speak | Kokoro container is stopped | Start `holly-kokoro-tts` and verify port `8881` |
| Launcher says Python is missing | Python path differs from the configured path | Update `PYTHONW` in `launch_holly_voice.bat` |
| Smoke check cannot connect | Server never reached port `5000` | Read `holly_server.log` and run `holly_server.py` visibly |

