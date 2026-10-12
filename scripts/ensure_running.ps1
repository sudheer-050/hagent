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

function Start-DetachedProcess([string]$filePath, [string]$arguments, [string]$workingDirectory) {
    # Start-Process fails when the inherited environment contains both Path and PATH.
    # ProcessStartInfo with shell execution avoids PowerShell's case-insensitive merge.
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $filePath
    $startInfo.Arguments = $arguments
    $startInfo.WorkingDirectory = $workingDirectory
    $startInfo.UseShellExecute = $true
    $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    [void][System.Diagnostics.Process]::Start($startInfo)
}

try {
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python executable not found: $python"
    }

    if (-not (Test-PortListening 8000)) {
        Start-DetachedProcess $python '-m hagent serve' $repoRoot
    }
} catch {
    Add-Content -LiteralPath $errorLog -Value "$(Get-Date -Format o) $($_.Exception.Message)"
    throw
}
