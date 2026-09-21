"""`summarize evaluate`: run the local evaluation corpus.

Kept separate from normal summarization: this command exists to compare the
same corpus across local models/backends, not to summarize a document. It
never downloads a model and never contacts anything but the configured local
endpoint. `--dry-run` lists the corpus without contacting an engine at all,
which is what CI uses.
"""

from __future__ import annotations

import sys

from summarizer.config import Config
from summarizer.engine import create_engine
from summarizer.errors import ConfigError
from summarizer.evaluation.cases import filter_cases, load_cases, resolve_eval_root
from summarizer.evaluation.report import render_text
from summarizer.evaluation.runner import run_cases
from summarizer.log import Diagnostics, redact_url

__all__ = ["run_evaluate"]

EXIT_CASES_FAILED = 1
EXIT_ENGINE = 2


def _parse_tags(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    tags = [tag.strip() for tag in raw.split(",") if tag.strip()]
    return tags or None


def run_evaluate(args, config: Config, diag: Diagnostics) -> int:
    paths = resolve_eval_root(getattr(args, "eval_dir", None))
    cases = load_cases(paths)
    cases = filter_cases(
        cases,
        ids=list(getattr(args, "eval_case_ids", []) or []) or None,
        tags=_parse_tags(getattr(args, "tags", None)),
    )
    if not cases:
        diag.error("no evaluation cases matched the selected ids/tags")
        return EXIT_CASES_FAILED

    if args.dry_run:
        print("Evaluation cases (dry run; no engine contacted)")
        print("")
        print(f"root:     {paths.root}")
        print(f"fixtures: {paths.fixtures}")
        for case in cases:
            tags = f" [{', '.join(case.tags)}]" if case.tags else ""
            print(f"  {case.id}{tags}  ({case.fixture}, profile={case.profile}, format={case.output_format})")
            if case.description:
                print(f"      {case.description}")
        print("")
        print(f"{len(cases)} case(s) ready. Run without --dry-run to execute.")
        return 0

    try:
        engine = create_engine(config, diag=diag)
    except ConfigError as exc:
        diag.error(exc.message)
        if exc.hint:
            diag.info(f"hint: {exc.hint}")
        return exc.exit_code

    if not engine.health():
        diag.error(
            f"engine {engine.backend!r} is not reachable at {redact_url(engine.endpoint)}"
        )
        diag.info("hint: start your local model server, then retry")
        return EXIT_ENGINE

    report = run_cases(
        cases,
        engine=engine,
        config=config,
        diag=diag,
        paths=paths,
        repeats=max(1, int(getattr(args, "repeats", 1) or 1)),
    )

    if (args.format or "").lower() == "json":
        sys.stdout.write(report.to_json_text())
    else:
        sys.stdout.write(render_text(report) + "\n")
    sys.stdout.flush()
    return 0 if report.passed else EXIT_CASES_FAILED
