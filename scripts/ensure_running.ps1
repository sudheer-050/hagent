# Start Hagent (dashboard + API) if its local port is not already listening.
# Called by the desktop launcher.
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$errorLog = Join-Path $repoRoot 'hagent-watchdog-error.log'
$portFile = Join-Path $repoRoot 'output\hagent-local-port.txt'

function Test-PortListening([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { $client.Connect('127.0.0.1', $port); return $true }
    catch { return $false }
    finally { $client.Dispose() }
}

function Test-HagentReady([int]$port) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 2
        return $response.Content -match '<title>[^<]*Hagent[^<]*</title>'
    } catch { return $false }
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

    $ports = @(8000..8009)
    if (Test-Path -LiteralPath $portFile) {
        $savedPort = 0
        if ([int]::TryParse((Get-Content -LiteralPath $portFile -Raw).Trim(), [ref]$savedPort) -and $savedPort -ge 8000 -and $savedPort -le 8009) {
            $ports = @($savedPort) + @($ports | Where-Object { $_ -ne $savedPort })
        }
    }
    # A listening port may belong to Docker or another app. Reuse only Hagent.
    $selectedPort = $ports | Where-Object { (Test-PortListening $_) -and (Test-HagentReady $_) } | Select-Object -First 1
    if (-not $selectedPort) {
        $selectedPort = $ports | Where-Object { -not (Test-PortListening $_) } | Select-Object -First 1
        if (-not $selectedPort) { throw 'No available local Hagent port in 8000-8009.' }
        Start-DetachedProcess $python "-m hagent serve --port $selectedPort" $repoRoot
        $ready = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            if (Test-HagentReady $selectedPort) { $ready = $true; break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw "Hagent did not become ready on port $selectedPort." }
    }
    [void](New-Item -ItemType Directory -Path (Split-Path -Parent $portFile) -Force)
    Set-Content -LiteralPath $portFile -Value $selectedPort -Encoding Ascii
} catch {
    Add-Content -LiteralPath $errorLog -Value "$(Get-Date -Format o) $($_.Exception.Message)"
    throw
}
