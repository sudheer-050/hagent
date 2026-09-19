from hagent.skill_icons import SKILL_EMOJI_GROUPS, choose_skill_emoji, skill_type


def test_skill_types_choose_matching_people():
    assert skill_type('Python code review') == 'coding'
    assert skill_type('Research evidence') == 'research'
    assert skill_type('Visual design') == 'design'
    assert choose_skill_emoji('Python code review') in SKILL_EMOJI_GROUPS['coding']


def test_many_similar_skills_have_distinct_people_icons():
    used = set()
    for _ in range(40):
        icon = choose_skill_emoji('Python debugging', used=used)
        assert icon not in used
        assert any(icon.startswith(person) for person in SKILL_EMOJI_GROUPS['coding'])
        used.add(icon)
