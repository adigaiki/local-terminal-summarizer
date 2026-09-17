"""The `summarize` command-line interface.

Unix contract:
  * stdout carries program output only (summary, strict JSON, reports).
  * stderr carries all diagnostics, progress, and warnings.
  * exit codes are stable: 0 ok, 1 input, 2 engine, 3 config, 4 update,
    130 interrupted.

Bare `summarize` never hangs on an interactive TTY: with no input argument
we explain and exit 1. `summarize -` is the explicit way to read stdin.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Callable, NoReturn, Sequence

from summarizer import __version__
from summarizer.config import Config, load_config
from summarizer.errors import SummarizerError
from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions
from summarizer.session import SessionManager

__all__ = ["main", "build_parser"]

EXIT_OK = 0
EXIT_INPUT = 1
EXIT_ENGINE = 2
EXIT_CONFIG = 3
EXIT_UPDATE = 4
EXIT_INTERRUPTED = 130


class _UsageErrorParser(argparse.ArgumentParser):
    """Argument parser whose usage errors use the documented exit code.

    argparse hard-codes exit status 2 for usage errors, but this program
    reserves 2 for local *engine* errors and documents 1 as the "input or
    usage error" status (see README "Exit status"). Overriding ``error``
    keeps that contract intact instead of silently colliding with it.
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_INPUT, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _UsageErrorParser(
        prog="summarize",
        description=(
            "Local-first terminal summarizer. Reads a document from a file "
            "or stdin and summarizes it with a local LLM server. Never "
            "requires the internet."
        ),
        epilog=(
            "Examples:\n"
            "  echo 'text' | summarize\n"
            "  summarize README.md --profile plain\n"
            "  git diff | summarize --profile code --quiet\n"
            "  summarize README.md --format json | jq\n"
            "  summarize README.md --dry-run\n"
            "  summarize doctor\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"summarize {__version__}")
    # Do not use argparse subparsers here.  An optional file positional and
    # subparsers are ambiguous: argparse will eagerly treat `README.md` as a
    # command and make the ordinary `summarize README.md` invocation fail.
    # We parse operands first and classify the two reserved management
    # commands below.
    parser.add_argument(
        "operands", nargs="*", default=[], metavar="INPUT",
        help="file to summarize; omit or use '-' to read stdin; commands: doctor, session",
    )
    parser.add_argument(
        "--profile", metavar="NAME", default=None,
        help="prompt profile (plain, code, academic, meeting, or a user "
             "profile from ~/.config/summarizer/prompts/)",
    )
    parser.add_argument("--format", dest="format", metavar="FORMAT", default=None,
                        help="output format: plain, markdown, or json")
    parser.add_argument("--model", metavar="NAME", default=None,
                        help="model name on the server")
    parser.add_argument("--endpoint", metavar="URL", default=None,
                        help="local LLM server endpoint")
    parser.add_argument("--backend", metavar="NAME", default=None,
                        help="engine backend: ollama or openai (OpenAI-compatible)")
    parser.add_argument("--no-stream", action="store_true",
                        help="do not stream output tokens live")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="suppress diagnostics on stderr")
    parser.add_argument("-o", "--output", metavar="FILE", default=None,
                        help="write output atomically to FILE instead of stdout")
    parser.add_argument("--lang", metavar="LANG", default=None,
                        help="request the summary in LANG (default: source language)")
    parser.add_argument(
        "--context", metavar="FILE", default=None,
        help="trusted supplemental context file; kept structurally separate "
             "from the untrusted document",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would happen; performs no LLM request")
    parser.add_argument("--timeout", dest="timeout_seconds", type=int, metavar="SECONDS",
                        default=None, help="per-request timeout in seconds")
    parser.add_argument("--retries", type=int, metavar="N", default=None,
                        help="retries for transient engine failures")
    parser.add_argument("--max-tokens", type=int, metavar="N", default=None,
                        help="maximum generated tokens per engine request")
    parser.add_argument(
        "--reasoning-effort", choices=("none", "low", "medium", "high"),
        default=None, help="reasoning budget for backends that support it (default: none)",
    )
    parser.add_argument("--strict", action="store_true",
                        help="error on oversized input instead of map-reduce chunking")
    parser.add_argument("--chunk-strategy", metavar="STRATEGY", default=None,
                        help="chunking metric: chars, tokens, or auto")
    parser.add_argument("--encoding", metavar="NAME", default=None,
                        help="input encoding (default: utf-8 with documented fallback)")
    parser.add_argument("--ocr", action="store_true",
                        help="enable OCR for scanned PDFs (optional dependencies)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="verbose diagnostics on stderr")
    parser.add_argument("--debug", action="store_true",
                        help="debug diagnostics (adds tracebacks)")
    parser.add_argument("--check-update", action="store_true",
                        help="check for a newer release (explicit network operation)")
    return parser


def _classify_operands(parser: argparse.ArgumentParser, args) -> None:
    """Classify ordinary input operands and the two management commands."""
    operands = args.operands
    args.command = None
    args.input = None
    if not operands:
        return
    if operands[0] == "doctor":
        if len(operands) != 1:
            parser.error("doctor does not accept additional operands")
        args.command = "doctor"
        return
    if operands[0] == "session":
        if len(operands) < 2:
            parser.error("session requires one of: start, end, status")
        action = operands[1]
        if action not in ("start", "end", "status"):
            parser.error("session action must be one of: start, end, status")
        if action == "start":
            if len(operands) != 3:
                parser.error("session start requires exactly one NAME")
            args.name = operands[2]
        elif len(operands) != 2:
            parser.error(f"session {action} does not accept additional operands")
        args.command = "session"
        args.session_action = action
        return
    if len(operands) != 1:
        parser.error("expected one input file, or `doctor` / `session` command")
    args.input = operands[0]



def _flush_writer(handle) -> Callable[[str], None]:
    def _write(text: str) -> None:
        handle.write(text)
        handle.flush()

    return _write


def _print_error(diag: Diagnostics, exc: BaseException) -> None:
    message = exc.message if isinstance(exc, SummarizerError) else str(exc)
    diag.error(message)
    hint = getattr(exc, "hint", None)
    if hint:
        diag.info(f"hint: {hint}")


def _resolve_config(args) -> Config:
    """Load configuration, applying CLI-level engine overrides on top."""
    config = load_config()
    overrides: dict[str, object] = {}
    if args.backend:
        overrides["backend"] = args.backend
    if args.endpoint:
        overrides["endpoint"] = args.endpoint
    if args.model:
        overrides["model"] = args.model
    if args.timeout_seconds:
        overrides["timeout_seconds"] = args.timeout_seconds
    if args.retries is not None:
        overrides["retries"] = args.retries
    if args.max_tokens is not None:
        overrides["max_tokens"] = args.max_tokens
    if args.reasoning_effort is not None:
        overrides["reasoning_effort"] = args.reasoning_effort
    if overrides:
        config = config.with_overrides(engine=overrides)
    return config


def _run_session(args, config: Config, diag: Diagnostics) -> int:
    from summarizer.session.manager import SessionManager

    manager = SessionManager(diag=diag)
    action = args.session_action
    if action == "start":
        manager.start(args.name)
        print(f"session {args.name!r} started (state: {manager.state_file})")
        return EXIT_OK
    if action == "end":
        ended = manager.end()
        if ended.name:
            print(f"session {ended.name!r} ended")
        else:
            print("no active session to end")
        return EXIT_OK
    if action == "status":
        status = manager.status()
        print(status.describe())
        print(f"notes dir: {config.session.notes_dir} (enabled={config.session.enabled})")
        return EXIT_OK
    raise ValueError(f"unknown session action {action!r}")


def _run_update_check(config: Config, diag: Diagnostics) -> int:
    from summarizer.update import UpdateManager

    manager = UpdateManager(config, diag=diag)
    report = manager.check()
    print(report.render())
    return EXIT_OK


def _run_summarize(args, config: Config, diag: Diagnostics) -> int:
    source = args.input
    if source == "-":
        source = None
    elif source is None and sys.stdin.isatty():
        # Never hang on an interactive TTY with no input.
        diag.error("no input provided")
        diag.info("hint: pipe input or provide a file; `summarize -` reads stdin explicitly")
        return EXIT_INPUT

    fmt = (args.format or config.defaults.output_format).lower()
    if fmt not in ("plain", "markdown", "json"):
        diag.error(f"unknown output format {fmt!r}")
        diag.info("hint: --format accepts plain, markdown, or json")
        return EXIT_INPUT

    opts = PipelineOptions(
        profile=args.profile or config.defaults.profile,
        output_format=fmt,
        model=args.model or config.engine.model,
        endpoint=args.endpoint or config.engine.endpoint,
        backend=args.backend,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
        stream=(not args.no_stream and config.defaults.stream and fmt != "json"),
        strict=args.strict,
        chunk_strategy=args.chunk_strategy or None,
        lang=args.lang or config.defaults.lang,
        encoding=args.encoding or None,
        context_file=args.context,
        ocr=args.ocr,
    )
    pipeline = Pipeline(config, diag=diag)
    will_stream = opts.stream and fmt != "json"

    if args.dry_run:
        report = pipeline.dry_run(source, opts=opts)
        text = report.render()
        if args.output and args.output != "-":
            from summarizer.output.atomic import atomic_write

            with atomic_write(args.output) as handle:
                handle.write(text)
        else:
            sys.stdout.write(text)
            sys.stdout.flush()
        return EXIT_OK

    if args.output and args.output != "-":
        from summarizer.output.atomic import atomic_write

        with atomic_write(args.output) as handle:
            emitter = _flush_writer(handle) if will_stream else None
            result = pipeline.run(source, opts=opts, emit=emitter)
            if not will_stream:
                handle.write(result.text)
            elif isinstance(result.summary, str) and not result.summary.endswith("\n"):
                handle.write("\n")
            handle.flush()
    else:
        emitter = _flush_writer(sys.stdout) if will_stream else None
        result = pipeline.run(source, opts=opts, emit=emitter)
        if not will_stream:
            sys.stdout.write(result.text)
        elif isinstance(result.summary, str) and not result.summary.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()

    _maybe_record_session_note(result, config, diag)
    return EXIT_OK


def _maybe_record_session_note(result, config: Config, diag: Diagnostics) -> None:
    """Append an opt-in session note; never fatal, never leaks a full summary."""
    if not config.session.enabled:
        return
    try:
        manager = SessionManager(diag=diag)
        if not manager.status().active:
            return
        from summarizer.session.digest import append_note, make_note

        notes_dir = Path(config.session.notes_dir).expanduser()
        summary = result.summary if isinstance(result.summary, str) else str(result.summary)
        note = make_note(
            source=result.document.source,
            profile=result.profile.name,
            summary=summary,
        )
        append_note(
            notes_dir,
            note,
            digest_format=config.session.digest_format,
            append_mode=config.session.append_mode,
            diag=diag,
        )
        diag.verbose_message(f"session note appended to {notes_dir}")
    except Exception as exc:  # session notes must never break summarization
        diag.warn(f"could not write session note: {exc}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _classify_operands(parser, args)

    diag = Diagnostics(quiet=args.quiet, verbose=args.verbose, debug=args.debug)

    if args.timeout_seconds is not None and args.timeout_seconds < 1:
        diag.error("--timeout must be at least 1 second")
        return EXIT_INPUT
    if args.retries is not None and not 0 <= args.retries <= 10:
        diag.error("--retries must be between 0 and 10")
        return EXIT_INPUT
    if args.max_tokens is not None and args.max_tokens < 1:
        diag.error("--max-tokens must be at least 1")
        return EXIT_INPUT

    # Malformed configuration must produce a useful error (exit 3).
    try:
        config = _resolve_config(args)
    except SummarizerError as exc:
        _print_error(diag, exc)
        return exc.exit_code
    except Exception as exc:
        diag.error(f"could not load configuration: {exc}")
        if args.debug:
            traceback.print_exc()
        return EXIT_CONFIG

    try:
        if args.command == "doctor":
            from summarizer.doctor import run_doctor

            return run_doctor(config, diag=diag)
        if args.command == "session":
            return _run_session(args, config, diag)
        if args.check_update:
            return _run_update_check(config, diag)
        return _run_summarize(args, config, diag)
    except SummarizerError as exc:
        _print_error(diag, exc)
        if args.debug:
            traceback.print_exc()
        return exc.exit_code
    except KeyboardInterrupt:
        diag.error("interrupted")
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        # A downstream consumer (e.g. `head`) closed the pipe; exit quietly.
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 141  # strict SIGPIPE convention
    except Exception as exc:
        diag.error(f"unexpected error: {type(exc).__name__}: {exc}")
        diag.info("hint: run with --debug for a traceback, and please report this")
        if args.debug:
            traceback.print_exc()
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
