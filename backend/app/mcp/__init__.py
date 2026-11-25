"""Claude as a custom connector (MCP): Claude calls tools on https://APP_DOMAIN/mcp
and gets compact text back, instead of reading the page through the browser.

    oauth.py    the login Claude goes through once (OAuth 2.1, PKCE, client registration)
    auth.py     every /mcp request: its bearer token -> one of the allowed accounts
    server.py   the MCP server and its tools

Only with MCP_ENABLED=true, and only for the accounts MCP_USERNAMES names
(athletes, or coaches working on their linked athletes): nobody else's data
is reachable this way. Mounted by main.py.
"""
