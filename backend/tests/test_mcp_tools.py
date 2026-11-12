"""Claude's connector end to end (app/mcp/auth.py, app/mcp/server.py): /mcp
refuses requests without a valid token in the way Claude expects, and with
one it lists and runs the tools as the connected account."""

import json

from test_mcp_oauth import connect

from app.mcp import auth as mcp_auth
from app.mcp.server import build_server

ACCEPT = {"Accept": "application/json, text/event-stream"}


def _call(client, tokens, method, params=None, path="/mcp"):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    res = client.post(path, json=body, headers={**ACCEPT, "Authorization": f"Bearer {tokens['access_token']}"})
    assert res.status_code == 200, res.text
    return res.json()["result"]


def test_without_a_token_mcp_points_to_the_login(client):
    for headers in ({}, {"Authorization": "Bearer made-up"}, {"Authorization": "Basic abc"}):
        for path in ("/mcp", "/mcp/"):
            res = client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                              headers={**ACCEPT, **headers}, follow_redirects=False)
            assert res.status_code == 401, (path, headers)
            assert res.headers["www-authenticate"] == (
                'Bearer resource_metadata="http://testserver/.well-known/oauth-protected-resource"')
            assert res.headers["cache-control"] == "no-store"
    assert client.get("/mcp").status_code == 401


def test_a_page_session_token_does_not_open_mcp(client, auth):
    """The connector only takes its own tokens, not the page's login."""
    res = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={**ACCEPT, **auth})
    assert res.status_code == 401


def test_probes_are_answered_without_a_token(client):
    assert client.options("/mcp").status_code == 204
    assert client.head("/mcp").status_code == 200


def test_tools_are_listed_and_run_as_the_connected_account(client, auth):
    _client_id, tokens = connect(client)
    init = _call(client, tokens, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                "clientInfo": {"name": "test", "version": "1"}})
    assert init["serverInfo"]["name"] == "training"

    tools = {tool["name"]: tool for tool in _call(client, tokens, "tools/list", path="/mcp/")["tools"]}
    assert "connection_check" in tools

    result = _call(client, tokens, "tools/call", {"name": "connection_check", "arguments": {}})
    assert result.get("isError") is not True
    assert "Connected to the Training App as athlete." in result["content"][0]["text"]
    assert mcp_auth.current_user_id.get() is None  # nothing left behind after the request


def test_every_tool_says_what_it_does_and_whether_it_changes_data():
    """Claude picks tools from their descriptions, and asks before running one that isn't read-only."""
    tools = build_server()._tool_manager.list_tools()
    assert tools
    for tool in tools:
        assert tool.description and len(tool.description) > 20, tool.name
        hints = json.loads(tool.annotations.model_dump_json()) if tool.annotations else {}
        assert hints.get("readOnlyHint") is True or hints.get("destructiveHint") is True, tool.name
