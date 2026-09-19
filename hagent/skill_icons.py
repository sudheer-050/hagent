"""Random but skill-relevant people emoji choices."""

import re
import secrets
from collections.abc import Iterable


_BASE_SKILL_EMOJI_GROUPS = {
    'coding': ('👩‍💻', '👨‍💻', '🧑‍💻'),
    'research': ('👩‍🔬', '👨‍🔬', '🧑‍🔬'),
    'design': ('👩‍🎨', '👨‍🎨', '🧑‍🎨'),
    'teaching': ('👩‍🏫', '👨‍🏫', '🧑‍🏫'),
    'business': ('👩‍💼', '👨‍💼', '🧑‍💼'),
    'health': ('👩‍⚕️', '👨‍⚕️', '🧑‍⚕️'),
    'cooking': ('👩‍🍳', '👨‍🍳', '🧑‍🍳'),
    'engineering': ('👩‍🔧', '👨‍🔧', '🧑‍🔧'),
    'farming': ('👩‍🌾', '👨‍🌾', '🧑‍🌾'),
    'safety': ('👩‍🚒', '👨‍🚒', '🧑‍🚒'),
    'travel': ('👩‍✈️', '👨‍✈️', '🧑‍✈️'),
}

_SKIN_TONES = ('', '🏻', '🏼', '🏽', '🏾', '🏿')
_BADGES = ('✨', '🌙', '⭐', '💫', '🔹', '🔸', '🟦', '🟪', '🟩')


def _variants(person: str) -> tuple[str, ...]:
    head, joiner, role = person.partition('\u200d')
    return tuple(f'{head}{tone}{joiner}{role}' for tone in _SKIN_TONES)


SKILL_EMOJI_GROUPS = {
    category: tuple(variant for person in people for variant in _variants(person))
    for category, people in _BASE_SKILL_EMOJI_GROUPS.items()
}

SKILL_KEYWORDS = {
    'coding': ('code', 'coding', 'programming', 'software', 'developer', 'debug', 'python', 'javascript', 'terminal', 'automation'),
    'research': ('research', 'researcher', 'science', 'scientific', 'experiment', 'analysis', 'analyze', 'data', 'facts', 'investigate'),
    'design': ('design', 'designer', 'graphic', 'illustration', 'creative', 'visual', 'art', 'ui', 'ux'),
    'teaching': ('teach', 'teaching', 'teacher', 'tutor', 'education', 'training', 'lesson'),
    'business': ('business', 'marketing', 'sales', 'finance', 'legal', 'strategy', 'planning', 'writing', 'editor', 'documentation'),
    'health': ('health', 'medical', 'medicine', 'clinical', 'patient', 'care'),
    'cooking': ('cook', 'cooking', 'recipe', 'food', 'chef', 'baking'),
    'engineering': ('engineer', 'engineering', 'repair', 'mechanic', 'hardware', 'build'),
    'farming': ('farm', 'farming', 'garden', 'gardening', 'agriculture', 'plants'),
    'safety': ('fire', 'firefighter', 'emergency', 'safety', 'rescue'),
    'travel': ('travel', 'flight', 'pilot', 'aviation', 'aircraft'),
}

PERSON_EMOJIS = tuple(emoji for group in SKILL_EMOJI_GROUPS.values() for emoji in group)


def skill_type(name: str = '', description: str = '', content: str = '') -> str | None:
    """Prefer the title and description, then use instructions as a fallback."""
    for source in (name, description, (content or '')[:500]):
        text = (source or '').casefold()
        for category, keywords in SKILL_KEYWORDS.items():
            if any(re.search(rf'\b{re.escape(keyword)}\b', text) for keyword in keywords):
                return category
    return None


def choose_skill_emoji(
    name: str = '', description: str = '', content: str = '', used: Iterable[str] = (),
) -> str:
    """Randomize within the matching profession, never reusing a visible icon."""
    category = skill_type(name, description, content)
    candidates = SKILL_EMOJI_GROUPS[category] if category else PERSON_EMOJIS
    taken = set(used)
    for pool in (
        candidates,
        tuple(f'{emoji}{badge}' for emoji in candidates for badge in _BADGES),
        PERSON_EMOJIS,
    ):
        available = tuple(emoji for emoji in pool if emoji not in taken)
        if available:
            return secrets.choice(available)
    return f'{secrets.choice(candidates)}✨{len(taken) + 1}'


def default_skill_emoji(context) -> str:
    """Assign an icon to skills created outside the web form as well."""
    values = context.get_current_parameters()
    return choose_skill_emoji(
        values.get('name', ''), values.get('description', ''), values.get('content', ''),
    )
