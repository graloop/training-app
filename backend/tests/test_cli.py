from conftest import login

from app import cli


def test_list_users(client, auth, capsys):
    assert cli.main(["list-users"]) == 0
    out = capsys.readouterr().out
    assert "admin" in out and "athlete" in out and "connected" in out


def test_reset_password_logs_out_and_the_new_one_works(client, auth, capsys):
    assert cli.main(["reset-password", "Athlete", "--password", "brand-new-pass"]) == 0
    assert "brand-new-pass" in capsys.readouterr().out
    assert client.get("/api/auth/me", headers=auth).status_code == 401  # old session ended
    login(client, "athlete", "brand-new-pass")


def test_reset_password_generates_one(client, auth, capsys):
    assert cli.main(["reset-password", "athlete"]) == 0
    password = capsys.readouterr().out.split("is now: ")[1].split()[0]
    assert len(password) == 16
    login(client, "athlete", password)


def test_reset_password_refuses_unknown_names_and_short_passwords(client, capsys):
    assert cli.main(["reset-password", "nobody"]) == 1
    assert cli.main(["reset-password", "admin", "--password", "short"]) == 1
    login(client, "admin", "admin-password")  # unchanged
