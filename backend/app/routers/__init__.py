"""The HTTP layer: one file per URL prefix, included in main.py.

Routers only translate HTTP into calls: they check who is calling (deps.py),
validate the body (schemas.py), call the real logic (planning/, garmin/,
accounts.py, sharing.py) and commit. They never import each other;
tests/test_structure.py checks that.
"""
