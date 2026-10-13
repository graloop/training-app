import logging
from pathlib import Path

from conftest import ADMIN, admin_auth, login

from app import accounts, models
from app.database import SessionLocal

REPO = Path(__file__).resolve().parents[2]


def _seed(monkeypatch, enabled=True, database_url=None):
    settings = accounts.get_settings()
    monkeypatch.setattr(settings, "seed_test_accounts", enabled)
    if database_url:
        monkeypatch.setattr(settings, "database_url", database_url)
    accounts.seed_test_accounts()


def _usernames():
    with SessionLocal() as db:
        return sorted(u.username for u in db.query(models.User))


def test_off_by_default(client):
    assert accounts.get_settings().seed_test_accounts is False
    assert _usernames() == ["admin"]  # only the ADMIN_USERNAME account from conftest
    assert client.post("/api/auth/login", json={"username": "user", "password": "useruser"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "adminadmin"}).status_code == 401


def test_seeding_creates_the_generic_admin_and_user(client, monkeypatch):
    _seed(monkeypatch)
    assert _usernames() == ["admin", "user"]

    admin = login(client, "admin", "adminadmin")
    me = client.get("/api/auth/me", headers=admin).json()
    assert me["is_admin"] is True
    assert client.get("/api/admin/users", headers=admin).status_code == 200

    user = login(client, "user", "useruser")
    assert client.get("/api/auth/me", headers=user).json()["is_admin"] is False
    assert client.get("/api/admin/users", headers=user).status_code == 403
    with SessionLocal() as db:
        for u in db.query(models.User):
            assert u.hashed_password.startswith("scrypt$") and "adminadmin" not in u.hashed_password


def test_seeding_is_idempotent_and_keeps_sessions(client, monkeypatch):
    _seed(monkeypatch)
    session = login(client, "user", "useruser")
    with SessionLocal() as db:
        before = {u.username: u.hashed_password for u in db.query(models.User)}
    _seed(monkeypatch)  # what every restart / --reload does
    with SessionLocal() as db:
        assert {u.username: u.hashed_password for u in db.query(models.User)} == before
    assert client.get("/api/auth/me", headers=session).status_code == 200  # not logged out by a reload
    assert _usernames() == ["admin", "user"]


def test_restart_restores_the_known_passwords_and_roles(client, monkeypatch):
    _seed(monkeypatch)
    user = login(client, "user", "useruser")
    assert client.post("/api/auth/password", json={"current_password": "useruser", "new_password": "something-else"},
                       headers=user).status_code == 200
    user_id = next(u["id"] for u in client.get("/api/admin/users", headers=login(client, "admin", "adminadmin")).json()["users"]
                   if u["username"] == "user")
    client.patch(f"/api/admin/users/{user_id}", json={"is_admin": True}, headers=login(client, "admin", "adminadmin"))

    _seed(monkeypatch)
    again = login(client, "user", "useruser")
    assert client.get("/api/auth/me", headers=again).json()["is_admin"] is False


def test_seeding_takes_over_the_env_admin_named_admin(client, monkeypatch):
    # conftest's ADMIN_USERNAME is also "admin": under npm start the test login wins.
    login(client, *ADMIN)
    _seed(monkeypatch)
    assert client.post("/api/auth/login", json={"username": "admin", "password": ADMIN[1]}).status_code == 401
    login(client, "admin", "adminadmin")


def test_other_accounts_are_untouched(client, monkeypatch):
    client.post("/api/admin/users", json={"username": "marie", "password": "marie-password"}, headers=admin_auth(client))
    _seed(monkeypatch)
    login(client, "marie", "marie-password")
    assert _usernames() == ["admin", "marie", "user"]


def test_refused_on_a_non_sqlite_database(client, monkeypatch, caplog):
    with caplog.at_level(logging.ERROR, logger="trainer.accounts"):
        _seed(monkeypatch, database_url="postgresql+psycopg2://trainer:x@db:5432/trainer")
    assert _usernames() == ["admin"]
    assert "only created on a local SQLite database" in caplog.text
    assert client.post("/api/auth/login", json={"username": "user", "password": "useruser"}).status_code == 401


def test_docker_stack_cannot_enable_test_accounts_and_requires_an_admin():
    import yaml

    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    env = compose["services"]["backend"]["environment"]
    assert "SEED_TEST_ACCOUNTS" not in env
    assert env["ADMIN_USERNAME"].startswith("${ADMIN_USERNAME:?")  # deploy fails with a clear message if unset
    assert env["ADMIN_PASSWORD"].startswith("${ADMIN_PASSWORD:?")
    assert "adminadmin" not in (REPO / "docker-compose.yml").read_text()


def test_npm_start_turns_test_accounts_on():
    script = (REPO / "scripts" / "dev.sh").read_text()
    assert 'export SEED_TEST_ACCOUNTS="${SEED_TEST_ACCOUNTS:-true}"' in script
