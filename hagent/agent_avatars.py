"""Agent avatars, via DiceBear's "bottts" style (https://www.dicebear.com/styles/bottts/,
open-source robot avatars by Pablo Stanley, free for personal and commercial
use). Replaces an earlier hand-drawn running-character rig: that custom art
didn't hold up next to a purpose-built, widely used avatar set, and its
light-on-transparent silhouette sat on the page's dark background as a
mismatched cutout rather than a portrait.

Avatars are fetched directly from DiceBear's public CDN by the browser
(`<img src=...>` in the templates) -- no local rendering, no API key, no
extra dependency. The seed is the agent's id, so the same agent always gets
the same robot body/color, and backgroundColor/backgroundType are pinned to
this app's own panel gradient (see --bg-panel/--bg-panel-hover in
style.css) so the badge matches the surrounding UI instead of clashing
with it.

An earlier version of this expressed an agent's activity state (resting /
running / thinking) with a small mood sticker badged onto the avatar's
corner. That put the expression next to the robot rather than on it. bottts
actually exposes its face parts as their own generator options (see
https://api.dicebear.com/9.x/bottts/schema.json) -- eyes and mouth can be
pinned to a specific style instead of left to the seed's random pick -- so
the mood is now the robot's own face: happy round eyes and a smile at rest,
a focused HUD-visor stare with a working mouth mid-run, dizzy x-eyes and a
flatlined mouth while queued and waiting. No separate icon, no overlay --
just a different face on the same robot.
"""

from urllib.parse import quote

DICEBEAR_BASE = "https://api.dicebear.com/9.x/bottts/svg"

# state (from web.py's _agent_activity_state) -> (eyes, mouth), picked from
# bottts's own enum of face parts. "frame"/"round frame" eyes read as
# goggles or glasses -- a deliberate choice so the busy face looks like a
# robot wearing focus goggles, not just a generic reskin.
_STATE_FACE = {
    "resting": ("happy", "smile01"),      # idle: content, relaxed smile
    "running": ("frame2", "grill01"),     # actively working: focus goggles, concentrating mouth
    "thinking": ("dizzy", "diagram"),     # queued/waiting: dazed x-eyes, tired flat mouth
}
_DEFAULT_FACE = ("round", "smile01")


def agent_avatar_url(seed: str, size: int = 64, state: str | None = None) -> str:
    """DiceBear API URL for an agent's avatar. `seed` should be the agent's
    id (stable even if the agent is renamed). `state` is the same
    resting/running/thinking value web.py's activity-state helpers compute;
    when given, it pins the robot's eyes and mouth to an expression for
    that state instead of leaving them to the seed's random draw."""
    eyes, mouth = _STATE_FACE.get(state, _DEFAULT_FACE)
    return (
        f"{DICEBEAR_BASE}?seed={quote(str(seed))}&size={size}"
        "&backgroundColor=142742,1a3453&backgroundType=gradientLinear&radius=50"
        f"&eyes[]={eyes}&mouth[]={mouth}"
    )
