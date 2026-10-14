"""Documentation link guard: relative links resolve and anchors exist.

The checker is `scripts/check_doc_links.py`; this proves it catches a broken
file link and a broken heading anchor, passes a good link and a good anchor,
ignores external URLs, and does not treat a fenced example as a link. No
network access is involved.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_doc_links.py"


def _load_checker(monkeypatch, root: Path):
    spec = importlib.util.spec_from_file_location("check_doc_links", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before execution so dataclasses can resolve string annotations.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", root)
    return module


def test_reports_broken_file_links_and_anchors(tmp_path, monkeypatch):
    checker = _load_checker(monkeypatch, tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").write_text(
        "# Title\n"                                        # 1
        "\n"                                               # 2
        "- good: [guide](docs/guide.md)\n"                 # 3
        "- good anchor: [step](docs/guide.md#the-step)\n"  # 4
        "- missing file: [gone](docs/gone.md)\n"           # 5
        "- missing anchor: [nope](docs/guide.md#not-there)\n"  # 6
        "- external: [site](https://example.invalid/docs)\n",   # 7
        encoding="utf-8",
    )
    (docs / "guide.md").write_text(
        "# Guide\n\n## The step\n\n```sh\n[example](docs/not-a-link.md)\n```\n",
        encoding="utf-8",
    )

    findings = checker.scan(tmp_path)

    assert [(f.path, f.line, f.rule, f.target) for f in findings] == [
        ("README.md", 5, "missing-file", "docs/gone.md"),
        ("README.md", 6, "missing-anchor", "docs/guide.md#not-there"),
    ]
