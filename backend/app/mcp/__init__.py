"""Claude as a custom connector (MCP): Claude calls tools on https://APP_DOMAIN/mcp
and gets compact text back, instead of reading the page through the browser.

    oauth.py    the login Claude goes through once (OAuth 2.1, PKCE, client registration)
    auth.py     every /mcp request: its bearer token -> the one allowed account
    server.py   the MCP server and its tools

Only with MCP_ENABLED=true, and only for the regular account MCP_USERNAME:
nobody else's data is reachable this way. Mounted by main.py.
"""
