param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$source = [System.Drawing.Image]::FromFile((Join-Path $PSScriptRoot '../../hagent/static/holly_icon.png'))
try {
    foreach ($item in @(@('StoreLogo',50),@('Square44',44),@('Square150',150))) {
        $bitmap = New-Object System.Drawing.Bitmap ([int]$item[1]),([int]$item[1])
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.DrawImage($source,0,0,[int]$item[1],[int]$item[1])
            $bitmap.Save((Join-Path $Destination ($item[0]+'.png')),[System.Drawing.Imaging.ImageFormat]::Png)
        } finally { $graphics.Dispose(); $bitmap.Dispose() }
    }
} finally { $source.Dispose() }
