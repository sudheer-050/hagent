# Stop Hagent (dashboard + API) cleanly.
#
# On Windows, a venv's python.exe is a thin redirector stub: it launches the
# real interpreter from the base install as a child process and waits on it.
# That child is the one that actually binds the port and does the work; the
# stub does nothing itself. Ending only one of them in Task Manager leaves
# the other running as an orphan that still holds the port - matching by
# command line instead of by image path catches both halves every time.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'hagent serve' } |
    ForEach-Object {
        Write-Output "Stopping PID $($_.ProcessId): $($_.CommandLine)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
