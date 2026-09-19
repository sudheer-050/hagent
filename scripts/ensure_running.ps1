# Start Hagent (dashboard + API) if its local port is not already listening.
# Called by the desktop launcher.
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$errorLog = Join-Path $repoRoot 'hagent-watchdog-error.log'

function Test-PortListening([int]$port) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

try {
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python executable not found: $python"
    }

    if (-not (Test-PortListening 8000)) {
        Start-Process -FilePath $python `
            -ArgumentList '-m', 'hagent', 'serve' `
            -WorkingDirectory $repoRoot `
            -RedirectStandardOutput (Join-Path $repoRoot 'hagent-server.log') `
            -RedirectStandardError (Join-Path $repoRoot 'hagent-server-error.log') `
            -WindowStyle Hidden
    }
} catch {
    Add-Content -LiteralPath $errorLog -Value "$(Get-Date -Format o) $($_.Exception.Message)"
    throw
}
