# Regenerates Holly's avatar only, via Codex CLI's built-in image tool.
# Run this yourself from a normal terminal - an unattended `codex exec` spawns
# an autonomous agent, which Claude Code's sandboxed session is not permitted
# to launch.
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\regenerate_holly_avatar.ps1

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$avatarDir = Join-Path $repoRoot 'hagent\static\avatars'
New-Item -ItemType Directory -Force -Path $avatarDir | Out-Null

$baseStyle = "Photorealistic professional corporate headshot portrait, studio lighting, plain neutral light-gray background, shoulders-up, direct eye contact with camera, warm confident expression, shallow depth of field, shot on 85mm lens, high detail skin texture, natural color grading, no text, no watermark, no illustration or cartoon style, image centered in a circular frame with a solid light-gray background outside the circle, portrait fills the circle edge-to-edge."

$line = "Woman, 24 years old, long straight blonde hair, bright wide genuinely happy smile showing teeth, sparkling joyful eyes, warm radiant wholesome energy, wearing a smart casual blouse."

$outPath = Join-Path $avatarDir "holly.png"
$prompt = "$baseStyle $line"
Write-Output "=== Generating holly -> $outPath ==="

$task = "Use your built-in image generation tool (not the CLI/API fallback, no API key needed) to generate ONE image with this prompt: '$prompt' Then move or copy the generated PNG to $outPath (overwrite if it already exists). Just confirm the file was saved and its size."

codex exec --dangerously-bypass-approvals-and-sandbox $task

if (Test-Path $outPath) {
    Write-Output "OK: $outPath ($((Get-Item $outPath).Length) bytes)"
} else {
    Write-Warning "holly.png was not produced"
}
