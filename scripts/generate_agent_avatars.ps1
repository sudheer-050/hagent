# Generates one photorealistic headshot per Hagent agent via Codex CLI (OpenAI
# Images API, using Codex's own configured credentials) and saves each PNG to
# hagent/static/avatars/<key>.png. Run this yourself from a normal terminal -
# an unattended `codex exec` spawns an autonomous agent, which Claude Code's
# sandboxed session is not permitted to launch.
#
# --dangerously-bypass-approvals-and-sandbox is required here (not the
# nonexistent --full-auto) because the task needs network access to call the
# OpenAI Images API, which Codex's default sandbox blocks.
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\generate_agent_avatars.ps1

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$avatarDir = Join-Path $repoRoot 'hagent\static\avatars'
New-Item -ItemType Directory -Force -Path $avatarDir | Out-Null

$baseStyle = "Photorealistic professional corporate headshot portrait, studio lighting, plain neutral light-gray background, shoulders-up, direct eye contact with camera, warm confident expression, shallow depth of field, shot on 85mm lens, high detail skin texture, natural color grading, no text, no watermark, no illustration or cartoon style, image centered in a circular frame with a solid light-gray background outside the circle, portrait fills the circle edge-to-edge."

$agents = @(
    @{ key = "anna";  line = "Woman in her early 30s, dark-rimmed glasses, hair in a loose bun, wearing a soft knit sweater, thoughtful intelligent expression." }
    @{ key = "holly"; line = "Woman, 24 years old, long straight blonde hair, bright wide genuinely happy smile showing teeth, sparkling joyful eyes, warm radiant wholesome energy, wearing a smart casual blouse." }
    @{ key = "mika";  line = "Woman in her mid-30s, short sleek black bob haircut, East Asian features, wearing a tailored navy blazer, calm authoritative expression." }
    @{ key = "jack";  line = "Man in his late 20s, short tousled hair, light stubble, wearing a plain charcoal t-shirt, relaxed focused expression." }
    @{ key = "luke";  line = "Man in his mid-20s, light brown hair, clean-cut, wearing a casual button-down shirt, friendly approachable smile." }
    @{ key = "ruth";  line = "Woman in her late 30s, short practical haircut, sharp attentive expression, wearing a dark blazer over a plain top." }
    @{ key = "mark";  line = "Man in his 40s, short greying hair, trimmed beard, wearing a grey quarter-zip pullover, steady reliable expression." }
    @{ key = "kate";  line = "Woman in her late 20s, long straight blonde hair, bright engaged expression, wearing a smart-casual blazer." }
    @{ key = "noah";  line = "Man in his early 30s, dark hair, glasses, wearing a plain navy polo shirt, vigilant composed expression." }
    @{ key = "josh";  line = "Man in his mid-20s, curly hair, energetic approachable smile, wearing a casual blue shirt." }
    @{ key = "grace"; line = "Woman in her 30s, natural curly hair, polished professional appearance, wearing a tailored blouse, encouraging smile." }
    @{ key = "sam";   line = "Woman in her late 20s, short practical haircut, wearing a plain dark sweater, analytical focused expression." }
    @{ key = "adam";  line = "Man in his late 30s, short neat hair, light beard, wearing a crisp collared shirt, discerning serious-but-friendly expression." }
    @{ key = "ben";   line = "Man in his 20s, short hair, glasses, wearing a casual grey t-shirt, meticulous attentive expression." }
    @{ key = "owen";  line = "Man in his 30s, short dark hair, light stubble, wearing a plain black crewneck, focused no-nonsense expression." }
)

foreach ($agent in $agents) {
    $outPath = Join-Path $avatarDir "$($agent.key).png"
    $prompt = "$baseStyle $($agent.line)"
    Write-Output "=== Generating $($agent.key) -> $outPath ==="

    $task = "Use your built-in image generation tool (not the CLI/API fallback, no API key needed) to generate ONE image with this prompt: '$prompt' Then move or copy the generated PNG to $outPath (overwrite if it already exists). Just confirm the file was saved and its size."

    codex exec --dangerously-bypass-approvals-and-sandbox $task

    if (-not (Test-Path $outPath)) {
        Write-Warning "$($agent.key): no file produced at $outPath - check Codex output above."
    }
}

Write-Output "Done. Generated files are in $avatarDir"
