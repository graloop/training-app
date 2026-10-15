"""Reaching the app from the internet: one public https address, and limits on
guessing at the login page."""

import re
from pathlib import Path

import pytest
from conftest import ADMIN, USER_PASSWORD, admin_auth, login, make_user
from fastapi import Request
from fastapi.testclient import TestClient

from app import accounts, main, models, visitor
from app.config import Settings
from app.database import SessionLocal
from app.routers import auth

REPO = Path(__file__).resolve().parents[2]

DOMAIN = "training.example.com"
VIA_HTTPS = {"Host": DOMAIN, "X-Forwarded-Proto": "https"}
VIA_HTTP = {"Host": DOMAIN, "X-Forwarded-Proto": "http"}


@pytest.fixture
def public(monkeypatch):
    """APP_DOMAIN is set, as in the hosted stack."""
    monkeypatch.setattr(accounts.get_settings(), "app_domain", DOMAIN)


# --- APP_DOMAIN: one https address -----------------------------------------------


def test_without_a_domain_any_address_works(client, proxied):
    for res in (client.get("/"), proxied.get("/", headers={"Host": "192.0.2.10:8010"})):
        assert res.status_code == 200
        assert "strict-transport-security" not in res.headers


def test_a_pasted_url_is_reduced_to_the_name():
    for typed in (DOMAIN, f" https://{DOMAIN}/ ", "HTTP://Training.Example.com/login", f"{DOMAIN}:443"):
        assert Settings(app_domain=typed).app_domain == DOMAIN
    assert Settings(app_domain="  ").app_domain == ""


def test_the_public_address_is_served_and_remembered_as_https(proxied, public):
    res = proxied.get("/", headers=VIA_HTTPS)
    assert res.status_code == 200 and 'id="auth-username"' in res.text
    # Not includeSubDomains: other names of the domain may be plain-http services at home.
    assert res.headers["strict-transport-security"] == f"max-age={main.HSTS_SECONDS}"
    assert proxied.get("/api/health", headers=VIA_HTTPS).json() == {"status": "ok"}


def test_plain_http_is_sent_to_https(proxied, public):
    res = proxied.get("/index.html?from=bookmark", headers=VIA_HTTP, follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == f"https://{DOMAIN}/index.html?from=bookmark"
    assert "strict-transport-security" not in res.headers


def test_other_addresses_are_sent_to_the_public_one(proxied, public):
    for host in ("192.0.2.10:8010", "192.0.2.10", "other.example.com"):
        res = proxied.get("/", headers={"Host": host, "X-Forwarded-Proto": "https"}, follow_redirects=False)
        assert (res.status_code, res.headers["location"]) == (307, f"https://{DOMAIN}/")


def test_a_password_is_not_taken_over_plain_http(client, proxied, public):
    body = {"username": ADMIN[0], "password": ADMIN[1]}
    res = proxied.post("/api/auth/login", json=body, headers=VIA_HTTP, follow_redirects=False)
    assert res.status_code == 400  # not a redirect: the browser would send the password again
    assert res.json()["detail"] == f"This app is only available at https://{DOMAIN}"
    with SessionLocal() as db:
        assert db.query(models.LoginEvent).count() == 0
    assert proxied.post("/api/auth/login", json=body, headers=VIA_HTTPS).status_code == 200


def test_the_server_itself_still_reaches_the_app(proxied, public):
    for host in ("localhost:8010", "127.0.0.1:8000", "[::1]:8000"):
        assert proxied.get("/api/health", headers={"Host": host}, follow_redirects=False).status_code == 200


def test_https_is_only_believed_from_the_proxy(client, public):
    # Port 8010 opened straight to the internet: the headers prove nothing.
    direct = TestClient(main.app, client=("8.8.8.8", 40000))
    assert direct.get("/", headers=VIA_HTTPS, follow_redirects=False).status_code == 307


def _raw_get(path, host="192.0.2.10:8010"):
    """A request as the server receives it, before any client tidies the path."""
    import asyncio

    messages = []
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(b"host", host.encode())], "client": ("172.18.0.3", 40000), "server": ("192.0.2.10", 8010),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(main.app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}


def test_the_redirect_always_points_at_the_public_name(public):
    from urllib.parse import urlsplit

    for path in ("@evil.example", "@evil.example/x", "//evil.example/x", "/ok"):
        status, headers = _raw_get(path)
        assert status == 307, path
        assert urlsplit(headers["location"]).hostname == DOMAIN, (path, headers["location"])


def test_cloudflare_in_front_of_a_reverse_proxy(proxied, public):
    """The proxy reports its own hop from Cloudflare (http) in X-Forwarded-Proto;
    redirecting on that alone would loop forever."""
    res = proxied.get("/", headers={**VIA_HTTP, "CF-Visitor": '{"scheme":"https"}'}, follow_redirects=False)
    assert res.status_code == 200
    res = proxied.get("/", headers={**VIA_HTTP, "CF-Visitor": '{"scheme":"http"}'}, follow_redirects=False)
    assert res.status_code == 307


# --- Headers every page carries ----------------------------------------------------


def test_range_requests_get_the_whole_file(client):
    """Starlette 0.46 can be made to burn CPU with a crafted Range header
    (CVE-2025-62727), and the page never asks for part of a file."""
    whole = client.get("/app.js")
    crafted = "bytes=" + ",".join(f"{i}-{i + 1}" for i in range(0, 4000, 2))
    for value in ("bytes=0-9", crafted):
        res = client.get("/app.js", headers={"Range": value})
        assert res.status_code == 200 and res.content == whole.content
        assert "content-range" not in res.headers


def test_pages_only_run_their_own_script(client):
    for path in ("/", "/app.js", "/api/health"):
        assert client.get(path).headers["content-security-policy"] == main.CONTENT_SECURITY_POLICY
    assert "'unsafe-inline'" not in main.CONTENT_SECURITY_POLICY and "default-src 'self'" in main.CONTENT_SECURITY_POLICY
    assert client.get("/").headers["x-robots-tag"] == "noindex, nofollow"


def test_the_page_has_nothing_that_policy_would_block():
    """No inline script, style or event handler, and nothing loaded from
    another site: any of these would silently stop working in the browser."""
    frontend = REPO / "backend" / "frontend"
    html = (frontend / "index.html").read_text()
    script = (frontend / "app.js").read_text()
    assert re.findall(r"<script(?![^>]*\bsrc=)", html) == []
    assert "<style" not in html
    for markup in (html, script):
        assert re.findall(r"""\s(?:style|on[a-z]+)=["']""", markup) == []
        assert re.findall(r"""(?:src|href)=["'](?:https?:)?//""", markup) == []
    assert "url(" not in (frontend / "styles.css").read_text()


# --- Who is visiting ----------------------------------------------------------------


def _request(peer, **headers):
    return Request({
        "type": "http", "method": "GET", "path": "/", "query_string": b"", "scheme": "http",
        "server": ("testserver", 80), "client": (peer, 40000) if peer else None,
        "headers": [(name.replace("_", "-").encode(), value.encode()) for name, value in headers.items()],
    })


def test_the_visitor_address_comes_from_the_proxy():
    tunnel = "172.18.0.3"
    assert visitor.address(_request(tunnel, cf_connecting_ip="203.0.113.9", x_forwarded_for="10.9.9.9, 203.0.113.9")) == "203.0.113.9"
    assert visitor.address(_request(tunnel, x_real_ip="203.0.113.9", x_forwarded_for="10.9.9.9, 198.51.100.1")) == "203.0.113.9"
    # X-Forwarded-For: the proxy adds the visitor last; what comes before was sent by the visitor.
    assert visitor.address(_request(tunnel, x_forwarded_for="10.9.9.9, 203.0.113.9")) == "203.0.113.9"
    assert visitor.address(_request(tunnel, cf_connecting_ip="not an address")) == tunnel
    assert visitor.address(_request(tunnel)) == tunnel
    assert visitor.address(_request("127.0.0.1", x_real_ip="2001:db8::7")) == "2001:db8::7"


def test_a_direct_visitor_cannot_choose_their_address():
    assert visitor.address(_request("8.8.8.8", cf_connecting_ip="203.0.113.9", x_real_ip="203.0.113.9")) == "8.8.8.8"
    assert visitor.address(_request(None, x_real_ip="203.0.113.9")) == "unknown"


# --- Guessing at the login page -----------------------------------------------------


def _try(proxied, username, password, address):
    return proxied.post("/api/auth/login", json={"username": username, "password": password},
                        headers={"CF-Connecting-IP": address})


def test_one_address_cannot_keep_guessing_across_accounts(client, proxied, monkeypatch):
    make_user(client, "friend", garmin=False)
    for i in range(accounts.MAX_ADDRESS_FAILURES):
        assert _try(proxied, f"guess{i}", "nope", "203.0.113.50").status_code == 401

    # From there even the right password is now refused, without being checked...
    checked = []
    real_verify = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda *args: checked.append(args) or real_verify(*args))
    res = _try(proxied, "friend", USER_PASSWORD, "203.0.113.50")
    assert res.status_code == 429 and "Too many failed attempts" in res.json()["detail"]
    assert checked == []
    # ...while the friend, somewhere else, logs in as usual.
    assert _try(proxied, "friend", USER_PASSWORD, "203.0.113.51").status_code == 200
    assert len(checked) == 1


def test_logging_in_does_not_buy_more_guesses(client, proxied):
    make_user(client, "friend", garmin=False)
    for i in range(accounts.MAX_ADDRESS_FAILURES - 1):
        _try(proxied, f"guess{i}", "nope", "203.0.113.50")
    assert _try(proxied, "friend", USER_PASSWORD, "203.0.113.50").status_code == 200
    assert _try(proxied, "admin", "nope", "203.0.113.50").status_code == 401  # the 20th
    assert _try(proxied, "friend", USER_PASSWORD, "203.0.113.50").status_code == 429


def test_the_username_lockout_does_not_depend_on_the_address(client, proxied):
    make_user(client, "friend", garmin=False)
    for i in range(accounts.MAX_FAILURES):  # a new address every time
        assert _try(proxied, "friend", "nope", f"203.0.113.{i}").status_code == 401
    assert _try(proxied, "friend", USER_PASSWORD, "203.0.113.200").status_code == 429


# --- The admin password -------------------------------------------------------------


def test_a_short_admin_password_is_refused_when_the_account_is_created(client, monkeypatch):
    settings = accounts.get_settings()
    monkeypatch.setattr(settings, "admin_username", "boss")
    monkeypatch.setattr(settings, "admin_password", "x" * (accounts.ADMIN_PASSWORD_MIN - 1))
    with pytest.raises(RuntimeError, match=f"at least {accounts.ADMIN_PASSWORD_MIN} characters"):
        accounts.bootstrap_admin()
    with SessionLocal() as db:
        assert [u.username for u in db.query(models.User)] == ["admin"]

    # An admin that already has a password is left alone: ADMIN_PASSWORD isn't used any more.
    monkeypatch.setattr(settings, "admin_username", "admin")
    accounts.bootstrap_admin()
    assert client.get("/api/auth/me", headers=admin_auth(client)).json()["is_admin"] is True

    monkeypatch.setattr(settings, "admin_username", "boss")
    monkeypatch.setattr(settings, "admin_password", "x" * accounts.ADMIN_PASSWORD_MIN)
    accounts.bootstrap_admin()
    login(client, "boss", "x" * accounts.ADMIN_PASSWORD_MIN)


# --- The Docker stack ----------------------------------------------------------------


def test_the_tunnel_is_optional_in_the_docker_stack():
    import yaml

    services = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]
    assert services["backend"]["environment"]["APP_DOMAIN"] == "${APP_DOMAIN:-}"
    tunnel = services["cloudflared"]
    assert tunnel["profiles"] == ["tunnel"]  # only started with COMPOSE_PROFILES=tunnel
    # ":?" here would stop every deploy that doesn't use the tunnel.
    assert tunnel["environment"]["TUNNEL_TOKEN"] == "${CLOUDFLARE_TUNNEL_TOKEN:-}"
    assert "ports" not in tunnel
