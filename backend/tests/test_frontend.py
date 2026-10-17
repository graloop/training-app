"""The page and its script agree: nothing the script looks for is missing from the page."""

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
HTML = (FRONTEND / "index.html").read_text()
JS = (FRONTEND / "app.js").read_text()
IDS = set(re.findall(r'id="([^"]+)"', HTML))


def test_every_element_the_script_uses_is_on_the_page():
    used = set(re.findall(r'getElementById\("([^"$]+)"\)', JS))
    tabs = re.search(r"const TABS = \[([^\]]+)\]", JS).group(1)
    for tab in re.findall(r'"(\w+)"', tabs):
        used |= {f"tab-{tab}", f"tab-btn-{tab}"}
    profile = re.findall(r'const PROFILE_(?:NUMBERS|TEXTS) = \[([^\]]+)\]', JS)
    used |= {f"pf-{key}" for group in profile for key in re.findall(r'"(\w+)"', group)}
    assert sorted(used - IDS) == []


def test_ids_are_unique():
    ids = re.findall(r'id="([^"]+)"', HTML)
    assert sorted({i for i in ids if ids.count(i) > 1}) == []


def test_the_sync_button_never_waits_on_one_long_request():
    """A long catch-up would hit the proxy's time limit: the page starts a job and asks how it goes."""
    assert "/api/garmin/sync/start" in JS and "/api/garmin/sync/status" in JS
    assert '"/api/garmin/sync"' not in JS
