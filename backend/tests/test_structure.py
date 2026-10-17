"""The layout the README describes stays true: routers only handle HTTP, and
code shared between them lives outside routers/."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def _imports(path: Path) -> list[tuple[str, list[str]]]:
    """(module, names) for every `from X import names` in the file."""
    tree = ast.parse(path.read_text())
    return [
        ("." * node.level + (node.module or ""), [alias.name for alias in node.names])
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    ]


def test_routers_never_import_each_other():
    for path in (APP / "routers").glob("*.py"):
        for module, names in _imports(path):
            assert not (module.startswith(".") and not module.startswith("..")), (
                f"routers/{path.name} imports {names} from routers/{module.lstrip('.')}: "
                "move what they share to planning/, garmin/ or another module outside routers/"
            )


def test_no_module_imports_another_ones_private_names():
    for path in APP.rglob("*.py"):
        for module, names in _imports(path):
            private = [name for name in names if name.startswith("_") and not name.startswith("__")]
            assert not private, f"{path.relative_to(APP)} imports private {private} from {module}"
