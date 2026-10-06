"""Shape/count regression test for GET /team.

Spec (Kate): 200 response, JSON array of exactly 3 objects, each with
exactly the string fields name/role/fun_fact and no extras.
"""
from fastapi.testclient import TestClient

from team_endpoint import app

EXPECTED_FIELDS = {"name", "role", "fun_fact"}


def test_team_endpoint_shape_and_count():
    client = TestClient(app)
    response = client.get("/team")

    assert response.status_code == 200

    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 3

    for member in body:
        assert isinstance(member, dict)
        assert set(member.keys()) == EXPECTED_FIELDS
        for field in EXPECTED_FIELDS:
            assert isinstance(member[field], str)
            assert member[field] != ""
