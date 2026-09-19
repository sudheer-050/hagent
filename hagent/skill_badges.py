"""Flat SVG badge icons for the skills library, keyed to the same skill
category taxonomy as skill_icons.py's emoji picker.

The car-photo icons this replaces carried their own baked-in vignette/photo
background, so they always sat on the page as a dark rectangle instead of
blending into it. These badges are transparent, drawn only in flat fills and
1.6px strokes (matching the outline icons already used for buttons elsewhere
in the app -- see .icon-btn svg in style.css), so they read as part of the
UI rather than a sticker pasted on top of it.

A workspace commonly has a dozen skills in the same category (a stack of
"coding" or fallback "general" skills, say), and giving every one of them
the exact same pictogram in the exact same color made the list unscannable
-- you couldn't tell two rows apart at a glance without reading the text.
Each category now has two related-but-distinct glyphs, and each skill also
gets its accent color nudged a few degrees around the wheel. Both picks are
derived from a hash of the skill's own name/description/content, so a given
skill always renders the same way (no per-request randomness), but two
same-category skills reliably look different from each other.
"""

import colorsys
import hashlib

from hagent.skill_icons import skill_type

BADGE_COLORS = {
    'coding': '#6fd3a6',
    'research': '#6ba7f2',
    'design': '#e08fe0',
    'teaching': '#f2c46b',
    'business': '#e8a857',
    'health': '#f2818a',
    'cooking': '#f0a35e',
    'engineering': '#8fb4e0',
    'farming': '#7bc98f',
    'safety': '#f28f5e',
    'travel': '#7ec7d6',
    'general': '#9aa8c7',
}

_STROKE = 'stroke="{color}" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"'

# Each entry is (glyph_markup, (scale_x, scale_y)). The scale corrects for
# glyphs drawn at different natural proportions -- a wrench spanning
# corner-to-corner reads bigger than brackets drawn in the middle third of
# the same 24x24 viewBox unless stretched back out to a consistent ~16-unit
# footprint centered on (12, 12).
_GLYPH_VARIANTS = {
    'coding': [
        ('<path d="M9 8 4 12l5 4M15 8l5 4-5 4M13.5 6.5l-3 11" {s}/>', (1, 1.45)),
        ('<rect x="4" y="5" width="16" height="14" rx="2" {s}/><path d="M8 10.5l3 1.8-3 1.8M13.5 15h3" {s}/>', (1, 1)),
    ],
    'research': [
        (
            '<path d="M10 3h4M11 3v5l-4.2 7.3A1.6 1.6 0 0 0 8.2 18h7.6a1.6 1.6 0 0 0 1.4-2.4L13 8V3" {s}/>'
            '<path d="M8.6 13.5h6.8" {s}/>',
            (1.25, 1.15),
        ),
        ('<circle cx="10.5" cy="10.5" r="5.5" {s}/><path d="M14.8 14.8 19.5 19.5" {s}/>', (1, 1)),
    ],
    'design': [
        (
            '<path d="M12 4a8 8 0 1 0 0 16c1 0 1.7-.7 1.7-1.6 0-.45-.18-.85-.46-1.14-.28-.3-.46-.7-.46-1.14 0-.9.76-1.62 1.7-1.62H16A4 4 0 0 0 20 10.5C20 6.9 16.4 4 12 4Z" {s}/>'
            '<circle cx="8.2" cy="10.5" r="0.9" fill="{color}" stroke="none"/>'
            '<circle cx="11.4" cy="8" r="0.9" fill="{color}" stroke="none"/>'
            '<circle cx="15" cy="9" r="0.9" fill="{color}" stroke="none"/>',
            (1, 1),
        ),
        ('<path d="M4 20l1.3-4.6L15.5 5.2a2 2 0 0 1 2.8 2.8L8.1 18.3 4 20Z" {s}/><path d="M13.8 7.2l3 3" {s}/>', (1, 1)),
    ],
    'teaching': [
        (
            '<path d="M4 6.5 12 4l8 2.5-8 2.5-8-2.5Z" {s}/>'
            '<path d="M7 9v5c0 1.1 2.2 2 5 2s5-.9 5-2V9" {s}/>'
            '<path d="M20 6.5v5.5" {s}/>',
            (1, 1.3),
        ),
        ('<path d="M4 6c3-1.4 6-1.4 8 0v12c-2-1.4-5-1.4-8 0V6Z" {s}/><path d="M20 6c-3-1.4-6-1.4-8 0v12c2-1.4 5-1.4 8 0V6Z" {s}/>', (1, 1)),
    ],
    'business': [
        (
            '<rect x="4" y="8.5" width="16" height="10.5" rx="1.6" {s}/>'
            '<path d="M9 8.5V6.8c0-.7.6-1.3 1.3-1.3h3.4c.7 0 1.3.6 1.3 1.3v1.7" {s}/>'
            '<path d="M4 13h16" {s}/>',
            (1, 1.3),
        ),
        ('<path d="M5 19V11M11 19V5M17 19v-6" {s}/><path d="M4 19h16" {s}/>', (1, 1)),
    ],
    'health': [
        (
            '<path d="M12 19s-6.8-4.1-9-8.3C1.4 7.7 3 5 6 5c1.8 0 3.2 1 4 2.4C10.8 6 12.2 5 14 5c3 0 4.6 2.7 3 5.7-2.2 4.2-9 8.3-9 8.3Z" {s}/>'
            '<path d="M6.5 11h2.3l1-2 1.6 4 1-2H14" {s}/>',
            (1, 1),
        ),
        ('<circle cx="12" cy="12" r="8" {s}/><path d="M12 8v8M8 12h8" {s}/>', (1, 1)),
    ],
    'cooking': [
        (
            '<path d="M6 20h12" {s}/>'
            '<path d="M7 20v-5.4a5 5 0 0 1 10 0V20" {s}/>'
            '<path d="M7 12a2.2 2.2 0 0 1 1-4 2.4 2.4 0 0 1 4.6-.9A2.3 2.3 0 0 1 17 9a2.2 2.2 0 0 1 0 3" {s}/>',
            (1.3, 1.3),
        ),
        ('<path d="M8 4v7M11 4v7M8 7.5h3M9.5 11v9" {s}/><path d="M16 4c2.2 2.2 2.2 6.3 0 8.5-1 1-1.5 2-1.5 4V20" {s}/>', (1, 1)),
    ],
    'engineering': [
        ('<path d="M14.7 9.3a3 3 0 1 1-4.2 4.2L6 18l-2-2 4.5-4.5a3 3 0 0 1 4.2-4.2l-2.2 2.2 1.5 1.5 2.2-2.2Z" {s}/>', (1.15, 1.15)),
        (
            '<circle cx="12" cy="12" r="3" {s}/>'
            '<path d="M12 4.5v2.4M12 17.1v2.4M4.5 12h2.4M17.1 12h2.4M6.9 6.9l1.7 1.7M15.4 15.4l1.7 1.7M6.9 17.1l1.7-1.7M15.4 8.6l1.7-1.7" {s}/>',
            (1, 1),
        ),
    ],
    'farming': [
        (
            '<path d="M12 20V10" {s}/>'
            '<path d="M12 12c0-4 3-6.5 7-6.5C19 9.5 16 12 12 12Z" {s}/>'
            '<path d="M12 15c0-3-2.4-5-5.5-5C6.5 13.5 9 15.5 12 15Z" {s}/>',
            (1.2, 1.15),
        ),
        ('<circle cx="12" cy="7.5" r="3" {s}/><path d="M4 19.5c2.2-3 6.4-3 8.5 0 2.1-3 6.3-3 8.5 0" {s}/>', (1, 1)),
    ],
    'safety': [
        (
            '<path d="M12 4 5 6.5V11c0 4.4 3 7.7 7 9 4-1.3 7-4.6 7-9V6.5L12 4Z" {s}/>'
            '<path d="M9.3 12l1.9 1.9 3.5-3.9" {s}/>',
            (1.05, 1),
        ),
        ('<rect x="6" y="11" width="12" height="8" rx="1.6" {s}/><path d="M8.5 11V8a3.5 3.5 0 0 1 7 0v3" {s}/>', (1, 1)),
    ],
    'travel': [
        ('<path d="M3 13.5 20 5l-6.5 17-2.3-6.7L3 13.5Zm7.2 2.3L20 5" {s}/>', (1.05, 1.05)),
        ('<circle cx="12" cy="12" r="8" {s}/><path d="M15.3 8.7l-2.1 5.6-5.6 2.1 2.1-5.6 5.6-2.1Z" {s}/>', (1, 1)),
    ],
    'general': [
        (
            '<path d="M12 3.5 13.8 9.6 20 12l-6.2 2.4L12 20.5l-1.8-6.1L4 12l6.2-2.4Z" '
            'stroke="{color}" stroke-width="2" fill="none" stroke-linejoin="round"/>'
            '<circle cx="12" cy="12" r="1" fill="{color}" stroke="none"/>',
            (1.2, 1.2),
        ),
        ('<circle cx="12" cy="12" r="7" {s}/><circle cx="12" cy="12" r="2" fill="{color}" stroke="none"/>', (1, 1)),
    ],
}


# Keyword-matched icons for common, specific skill topics. The 12-way
# category taxonomy in skill_icons.skill_type() is coarse -- "ATS Resume
# Tailoring" and "Backend: Encryption Relay" both land in generic buckets
# even though they're about very different, very identifiable things. This
# table is checked first, matching lowercase substrings against the skill's
# name + description; the first tuple whose keyword appears wins. Only when
# nothing here matches does rendering fall back to the broader category
# glyphs above. Order matters -- more specific phrases are listed first so
# they aren't shadowed by a shorter, more common substring.
_KEYWORD_ICONS = [
    (('resume', 'ats '), 'resume', '#e8a857'),
    (('encrypt', 'secrets', 'harden'), 'lock', '#f2818a'),
    (('redis', 'postgres', 'database'), 'database', '#8fb4e0'),
    (('deploy',), 'rocket', '#f0a35e'),
    (('git-drift', 'merge safety', 'pr review', 'pull request', 'git '), 'git', '#6fd3a6'),
    (('verification', 'playbook', 'checklist'), 'checklist', '#6ba7f2'),
    (('document generation', 'documentation'), 'document', '#f2c46b'),
    (('ghost job', 'filtering'), 'funnel', '#7ec7d6'),
    (('honest counsel', 'counsel'), 'scales', '#e08fe0'),
    (('ml engineering', 'machine learning', 'ml patterns'), 'chip', '#8fb4e0'),
    (('conversational', 'tone', 'natural language'), 'chat', '#f2818a'),
    (('accessibility', 'mobile', 'pwa'), 'phone', '#7ec7d6'),
    (('prompt drafting', 'delegation', 'prompt'), 'quote', '#e08fe0'),
    (('failover', 'runtime routing', 'routing chain'), 'link', '#8fb4e0'),
    (('operating protocol', 'workspace operating'), 'gear', '#9aa8c7'),
    (('cost-aware', 'cost aware'), 'dollar', '#e8a857'),
    (('override', 'admin approval'), 'key', '#9aa8c7'),
    (('continuous improvement', 'continuous learning'), 'trend', '#7bc98f'),
    (('monitor', 'infrastructure'), 'pulse', '#f28f5e'),
]

_KEYWORD_GLYPHS = {
    'resume': ('<rect x="5" y="3.5" width="12" height="17" rx="1.5" {s}/><path d="M8 8h6M8 11.5h6" {s}/><circle cx="17.4" cy="17.4" r="3" {s}/><path d="M16.2 17.4l1 1 1.7-2" {s}/>', (1, 1)),
    'lock': ('<rect x="6" y="11" width="12" height="8" rx="1.6" {s}/><path d="M8.5 11V8a3.5 3.5 0 0 1 7 0v3" {s}/>', (1, 1)),
    'database': ('<ellipse cx="12" cy="6.2" rx="7" ry="2.4" {s}/><path d="M5 6.2v5.8c0 1.3 3.1 2.4 7 2.4s7-1.1 7-2.4V6.2" {s}/><path d="M5 12v5.8c0 1.3 3.1 2.4 7 2.4s7-1.1 7-2.4V12" {s}/>', (1, 1)),
    'rocket': ('<path d="M12 3.2c2.8 2 4.2 5.4 3.7 9.6l-1.9 1.9h-3.6l-1.9-1.9c-.5-4.2.9-7.6 3.7-9.6Z" {s}/><circle cx="12" cy="9.5" r="1.3" fill="{color}" stroke="none"/><path d="M9.6 14.7 7.3 19.5l2.8-1.1M14.4 14.7l2.3 4.8-2.8-1.1" {s}/>', (1, 1)),
    'git': ('<circle cx="7" cy="5.5" r="2" {s}/><circle cx="7" cy="18.5" r="2" {s}/><circle cx="17" cy="9.5" r="2" {s}/><path d="M7 7.5v9M7 12c0 2.6 3.4 2.6 7 2.6M17 11.5V7.5" {s}/>', (1, 1)),
    'checklist': ('<rect x="5" y="4" width="14" height="16" rx="1.6" {s}/><path d="M8 9.2 9.3 10.5 11.6 8" {s}/><path d="M8 15.2 9.3 16.5 11.6 14" {s}/><path d="M14 9h3M14 15.2h3" {s}/>', (1, 1)),
    'document': ('<rect x="6" y="3.5" width="12" height="17" rx="1.5" {s}/><path d="M9 8h6M9 11.5h6M9 15h4" {s}/>', (1, 1)),
    'funnel': ('<path d="M4 5h16l-6.2 7.6V18l-3.6 2v-7.4Z" {s}/>', (1, 1)),
    'scales': ('<path d="M12 3.5v17M8.2 20.5h7.6" {s}/><path d="M4.5 7h5.4M14.1 7h5.4" {s}/><path d="M4.5 7l-2.2 5A2.2 2.2 0 0 0 4.5 14.8 2.2 2.2 0 0 0 6.7 12L4.5 7ZM19.5 7l-2.2 5a2.2 2.2 0 0 0 2.2 2.8 2.2 2.2 0 0 0 2.2-2.8L19.5 7Z" {s}/>', (1, 1)),
    'chip': ('<rect x="8" y="8" width="8" height="8" rx="1.2" {s}/><path d="M12 3.2v3M12 17.8v3M3.2 12h3M17.8 12h3M6 6l1.8 1.8M16.2 16.2 18 18M6 18l1.8-1.8M16.2 7.8 18 6" {s}/>', (1, 1)),
    'chat': ('<path d="M4 6h16v10H9.5l-4 3.5V16H4Z" {s}/><path d="M8 10h8M8 13h5" {s}/>', (1, 1)),
    'phone': ('<rect x="7.5" y="3" width="9" height="18" rx="2" {s}/><path d="M10.5 18h3" {s}/>', (1, 1)),
    'quote': ('<path d="M5 15c0-4 2-7 6-8v3c-2 .7-3 2-3 4h3v5H5v-4Z" {s}/><path d="M13 15c0-4 2-7 6-8v3c-2 .7-3 2-3 4h3v5h-6v-4Z" {s}/>', (1, 1)),
    'link': ('<path d="M9.5 14.5 14.5 9.5" {s}/><path d="M11 6.3l1.2-1.2a3.5 3.5 0 0 1 5 5L16 11" {s}/><path d="M13 17.7l-1.2 1.2a3.5 3.5 0 0 1-5-5L8 12.7" {s}/>', (1, 1)),
    'gear': ('<circle cx="12" cy="12" r="3" {s}/><path d="M12 4.5v2.4M12 17.1v2.4M4.5 12h2.4M17.1 12h2.4M6.9 6.9l1.7 1.7M15.4 15.4l1.7 1.7M6.9 17.1l1.7-1.7M15.4 8.6l1.7-1.7" {s}/>', (1, 1)),
    'dollar': ('<path d="M12 3.5v17" {s}/><path d="M16.2 7.6c0-1.9-1.9-3-4.2-3s-4.2 1-4.2 2.6c0 1.7 1.6 2.2 4.2 2.8 2.6.6 4.2 1.3 4.2 3 0 1.6-1.9 2.7-4.2 2.7s-4.2-1-4.2-2.8" {s}/>', (1, 1)),
    'key': ('<circle cx="8" cy="12" r="4" {s}/><path d="M11.6 12H20M15.8 12v3M18.8 12v2" {s}/>', (1, 1)),
    'trend': ('<path d="M4 17l5-5 4 3 7-8" {s}/><path d="M15.5 6.2h4.5v4.5" {s}/>', (1, 1)),
    'pulse': ('<path d="M3 12h4l2-6.5 4 13 2-8.5 2 2h4" {s}/>', (1, 1)),
}


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _rgb_to_hex(rgb: tuple) -> str:
    return '#' + ''.join(f'{round(max(0.0, min(1.0, c)) * 255):02x}' for c in rgb)


def _shift_hue(hex_color: str, degrees: float) -> str:
    """Rotate a hex color's hue by `degrees`, keeping its lightness/saturation --
    a cheap way to give same-category badges a family resemblance (same base
    color) while still reading as individually distinct."""
    r, g, b = _hex_to_rgb(hex_color)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + degrees / 360) % 1.0
    return _rgb_to_hex(colorsys.hls_to_rgb(h, l, s))


def _seed(name: str, description: str, content: str) -> int:
    digest = hashlib.md5(f'{name}\x00{description}\x00{content[:200]}'.encode('utf-8', 'ignore')).hexdigest()
    return int(digest[:8], 16)


def _keyword_match(name: str, description: str):
    """Return (glyph_key, base_color) for the first keyword hit, or None."""
    haystack = f'{name} {description}'.lower()
    for keywords, glyph_key, color in _KEYWORD_ICONS:
        if any(kw in haystack for kw in keywords):
            return glyph_key, color
    return None


def skill_badge_svg(name: str = '', description: str = '', content: str = '', size: int = 36) -> str:
    """A self-contained, background-free <svg> badge for a skill row.

    Category comes from the same keyword match skill_icons.skill_type()
    already uses for the emoji picker, so a skill's icon and its (currently
    unused-in-UI) emoji default agree with each other if that ever changes.
    Within a category, the exact glyph and its color tint are picked
    deterministically from the skill's own name/description, so two skills
    in the same category look related but not identical.
    """
    seed = _seed(name, description, content)
    hit = _keyword_match(name, description)
    if hit:
        glyph_key, base_color = hit
        glyph_template, (sx, sy) = _KEYWORD_GLYPHS[glyph_key]
    else:
        category = skill_type(name, description, content) or 'general'
        base_color = BADGE_COLORS.get(category, BADGE_COLORS['general'])
        variants = _GLYPH_VARIANTS.get(category, _GLYPH_VARIANTS['general'])
        glyph_template, (sx, sy) = variants[seed % len(variants)]
    hue_shift = ((seed // 3) % 5 - 2) * 8  # -16, -8, 0, 8, 16 degrees
    color = _shift_hue(base_color, hue_shift)
    glyph = glyph_template.format(s=_STROKE.format(color=color), color=color)
    return (
        f'<svg class="skill-badge" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'xmlns="http://www.w3.org/2000/svg" role="presentation" aria-hidden="true">'
        f'<rect x="0.75" y="0.75" width="22.5" height="22.5" rx="7" '
        f'fill="{color}1a" stroke="{color}55" stroke-width="1"/>'
        f'<g transform="translate(12 12) scale({sx} {sy}) translate(-12 -12)">{glyph}</g>'
        f'</svg>'
    )
