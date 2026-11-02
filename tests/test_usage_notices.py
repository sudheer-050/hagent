from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from hagent import db, web
from hagent.provider_health import usage_limit_notice
from tests.test_agent_pages import local_db, seed_agent


@pytest.mark.parametrize('error,expected', [
    ('Codex failed: {"error":{"type":"usage_limit_reached","message":"Try again at 5:23 PM"}}',
     'Your tokens are used up. Please wait until 5:23 PM.'),
    ("You've hit your session limit - resets 5:20pm (America/New_York)",
     'Your tokens are used up. Please wait until 5:20 PM (America/New_York).'),
    ('usage_limit_reached', 'Your tokens are used up. Please try again later. No reset time was provided.'),
    ('429 too many requests', 'The provider has reached its request limit. Please try again later. No reset time was provided.'),
    ('insufficient credits', 'Your provider credits are used up. Add credits to continue.'),
    ('context length limit exceeded', 'This conversation is too long. Start a new conversation to continue.'),
    ('Connection refused', None),
])
def test_limit_notice_is_plain_and_does_not_guess_reset_time(error, expected):
    assert usage_limit_notice(error) == expected


def test_structured_provider_reset_time_is_used():
    stamp = 1793579400
    notice = usage_limit_notice('{"type":"usage_limit_reached","resets_at":' + str(stamp) + '}')
    expected_time = datetime.fromtimestamp(stamp, timezone.utc).astimezone().strftime('%I:%M %p %Z on %b %d').lstrip('0')
    assert notice == f'Your tokens are used up. Please wait until {expected_time}.'
    assert 'resets_at' not in notice and '{' not in notice


def test_chat_and_runtime_tests_use_the_same_plain_notice(local_db, mocker):
    agent_id, runtime_id, _ = seed_agent()
    error = RuntimeError('CLI failed: {"error":"usage_limit_reached", "message":"try again at 8:35 PM"}')
    mocker.patch.object(web, 'execute_agent', side_effect=error)
    reply, _, _, _ = web._run_chat_turn(agent_id, 'hi', None, None, None, [], None)
    assert reply == 'Your tokens are used up. Please wait until 8:35 PM.'
    mocker.patch('hagent.provider_health._probe', side_effect=error)
    result = TestClient(web.app).post(f'/runtimes/{runtime_id}/test').json()
    assert not result['ok'] and result['message'] == reply
    page = TestClient(web.app).get('/runtimes')
    assert reply in page.text
    assert 'usage_limit_reached' not in page.text
