import pytest
from click.testing import CliRunner

from hagent import auth
from hagent.cli import cli

PASSWORD = "correct horse battery"


def run_cli(*args, input=None):
    return CliRunner().invoke(cli, list(args), input=input)


def create_user(username="sudheer", role="member"):
    result = run_cli("auth", "user-create", username, "--role", role, "--password-stdin", input=PASSWORD + "\n")
    assert result.exit_code == 0, result.output
    return result


def test_first_user_becomes_owner_and_enables_sign_in(cli_db):
    assert "no (loopback-only" in run_cli("auth", "status").output

    result = create_user(role="member")

    assert "Created owner 'sudheer'" in result.output and "Sign-in is now required" in result.output
    assert "yes" in run_cli("auth", "status").output
    assert "sudheer" in run_cli("auth", "user-list").output


def test_password_can_be_entered_at_a_hidden_prompt(cli_db):
    result = run_cli("auth", "user-create", "sudheer", input=f"{PASSWORD}\n{PASSWORD}\n")

    assert result.exit_code == 0 and PASSWORD not in result.output


def test_weak_password_and_duplicates_are_rejected(cli_db):
    weak = run_cli("auth", "user-create", "sudheer", "--password-stdin", input="short\n")
    assert weak.exit_code != 0 and "at least" in weak.output

    create_user()
    dup = run_cli("auth", "user-create", "SUDHEER", "--password-stdin", input=PASSWORD + "\n")
    assert dup.exit_code != 0 and "already exists" in dup.output


def test_token_is_printed_once_and_listed_without_its_secret(cli_db):
    create_user()

    created = run_cli("auth", "token-create", "--user", "sudheer", "--name", "laptop")
    secret = created.stdout.strip().splitlines()[-1]

    assert created.exit_code == 0 and secret.startswith("hag_")
    listing = run_cli("auth", "token-list").output
    assert "laptop" in listing and "active" in listing and secret not in listing
    assert auth.authenticate_secret(secret) is not None

    token_id = listing.split()[0]
    assert run_cli("auth", "token-revoke", token_id).exit_code == 0
    auth.invalidate_caches()
    assert auth.authenticate_secret(secret) is None
    assert "revoked" in run_cli("auth", "token-list").output


def test_worker_tokens_and_unknown_users(cli_db):
    create_user()

    unnamed = run_cli("auth", "token-create", "--user", "sudheer", "--kind", "worker")
    assert unnamed.exit_code != 0 and "needs --name" in unnamed.output

    worker = run_cli("auth", "token-create", "--user", "sudheer", "--kind", "worker", "--name", "laptop")
    identity = auth.authenticate_secret(worker.stdout.strip().splitlines()[-1])
    assert (identity.kind, identity.token_name) == ("worker", "laptop")

    missing = run_cli("auth", "token-create", "--user", "ghost")
    assert missing.exit_code != 0 and "not found" in missing.output


def test_removing_a_user_revokes_their_tokens_but_not_the_last_owner(cli_db):
    create_user()
    create_user("guest")
    secret = run_cli("auth", "token-create", "--user", "guest").stdout.strip().splitlines()[-1]

    assert run_cli("auth", "user-remove", "guest").exit_code == 0
    auth.invalidate_caches()
    assert auth.authenticate_secret(secret) is None

    last = run_cli("auth", "user-remove", "sudheer")
    assert last.exit_code != 0 and "last owner" in last.output


# --- serve ------------------------------------------------------------------

@pytest.fixture()
def uvicorn_run(mocker):
    return mocker.patch("uvicorn.run")


def test_serve_on_loopback_needs_no_account(cli_db, uvicorn_run):
    assert run_cli("serve").exit_code == 0

    assert uvicorn_run.call_args.kwargs["host"] == "127.0.0.1"


def test_serve_refuses_to_listen_beyond_this_machine_without_accounts(cli_db, uvicorn_run):
    result = run_cli("serve", "--host", "0.0.0.0")

    assert result.exit_code != 0 and "auth user-create" in result.output
    uvicorn_run.assert_not_called()


def test_serve_beyond_loopback_with_accounts_warns_about_plain_http(cli_db, uvicorn_run):
    create_user()

    result = run_cli("serve", "--host", "0.0.0.0", "--port", "9000")

    assert result.exit_code == 0 and "plain HTTP" in result.output
    assert uvicorn_run.call_args.kwargs["host"] == "0.0.0.0" and uvicorn_run.call_args.kwargs["port"] == 9000


def test_serve_with_tls_passes_certificates_and_does_not_warn(cli_db, uvicorn_run, tmp_path):
    create_user()
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    cert.write_text("x"), key.write_text("x")

    result = run_cli("serve", "--host", "0.0.0.0", "--ssl-certfile", str(cert), "--ssl-keyfile", str(key))

    assert "plain HTTP" not in result.output
    assert uvicorn_run.call_args.kwargs["ssl_certfile"] == str(cert)


def test_serve_requires_both_tls_files(cli_db, uvicorn_run, tmp_path):
    cert = tmp_path / "c.pem"
    cert.write_text("x")

    result = run_cli("serve", "--ssl-certfile", str(cert))

    assert result.exit_code != 0 and "together" in result.output
