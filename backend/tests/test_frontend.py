"""The page and its scripts agree: nothing the scripts look for is missing from the page."""

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
HTML = (FRONTEND / "index.html").read_text()
JS_FILES = sorted((FRONTEND / "js").rglob("*.js"))
JS = "\n".join(path.read_text() for path in JS_FILES)
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


def test_every_file_the_page_links_exists():
    links = re.findall(r'(?:src|href)="/([\w/.-]+\.(?:js|css))"', HTML)
    assert "js/main.js" in links
    assert [link for link in links if not (FRONTEND / link).is_file()] == []


def test_every_module_import_points_to_a_file():
    """A typo in an import path stops the whole page, and only the browser would tell."""
    for path in JS_FILES:
        for target in re.findall(r'from "(\.[^"]+)"', path.read_text()):
            assert (path.parent / target).resolve().is_file(), f"{path.name} imports missing {target}"


def test_modules_never_import_each_other_in_a_circle():
    """Anything two views need goes in js/core/, so each file can be read on its own."""
    graph = {
        path.resolve(): [(path.parent / t).resolve() for t in re.findall(r'from "(\.[^"]+)"', path.read_text())]
        for path in JS_FILES
    }

    def visit(node, trail):
        assert node not in trail, "circular import: " + " -> ".join(p.name for p in [*trail, node])
        for target in graph[node]:
            visit(target, [*trail, node])

    for node in graph:
        visit(node, [])


def test_no_script_hides_the_translation_function():
    """A local variable named t hides t() from i18n.js: the page then fails
    with "t is not a function", as the admin dashboard once did."""
    for path in JS_FILES:
        text = path.read_text()
        if not re.search(r"import \{[^}]*\bt\b[^}]*\} from", text):
            continue
        assert re.findall(r"\b(?:const|let|var|function)\s+t\b|[(,]\s*t\s*[,)=]", text) == [], path.name
