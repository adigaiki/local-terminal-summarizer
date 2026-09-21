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
import os
import signal
import sys
import traceback
from pathlib import Path
from typing import Callable, NoReturn, Sequence

from summarizer import __version__
from summarizer.cancel import CancelToken
from summarizer.config import (
    SOURCE_BUILTIN,
    SOURCE_CLI,
    MAX_CONCURRENCY,
    Config,
    load_config,
)
from summarizer.engine.backends import canonical_backend_name, default_endpoint_for_backend
from summarizer.errors import ConfigError, SummarizerError
from summarizer.log import Diagnostics
from summarizer.pipeline import Pipeline, PipelineOptions
from summarizer.progress import build_progress
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
            "  summarize --stats article.md\n"
            "  summarize doctor\n"
            "  summarize models\n"
            "  summarize profiles\n"
            "  summarize config show\n"
            "  summarize cache status\n"
            "  summarize completions bash\n"
            "  summarize evaluate --model qwen3:8b\n"
            "  summarize session start research\n"
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
        help="file to summarize; omit or use '-' to read stdin; commands: "
             "doctor, models, profiles, config, cache, session, evaluate, completions",
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
                        help="engine backend adapter: ollama, openai-compatible "
                             "(alias openai), llama.cpp, or lmstudio")
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
    parser.add_argument("--concurrency", type=int, metavar="N", default=None,
                        help="bounded parallel map workers (default 1; max 8)")
    parser.add_argument("--cache", metavar="MODE", default=None,
                        help="cache mode override: off, read, write, or readwrite")
    parser.add_argument("--stats", action="store_true",
                        help="print execution statistics to stderr (and into "
                             "JSON output when --format json)")
    parser.add_argument("--no-progress", action="store_true",
                        help="disable the progress display (stderr)")
    parser.add_argument("--encoding", metavar="NAME", default=None,
                        help="input encoding (default: utf-8 with documented fallback)")
    parser.add_argument("--ocr", action="store_true",
                        help="enable OCR for scanned PDFs (optional dependencies)")
    parser.add_argument("--session", dest="session_name", metavar="NAME", default=None,
                        help="record this run into the named session (explicit selection)")
    parser.add_argument("--no-session", dest="no_session", action="store_true",
                        help="do not record this run into any session")
    parser.add_argument("--notes-path", dest="notes_path", metavar="PATH", default=None,
                        help="override the sessions root directory for this invocation")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="verbose diagnostics on stderr")
    parser.add_argument("--debug", action="store_true",
                        help="debug diagnostics (adds tracebacks)")
    parser.add_argument("--check-update", action="store_true",
                        help="check for a newer release (explicit network operation)")
    parser.add_argument("--repeats", type=int, metavar="N", default=1,
                        help="evaluate: run each case N times (default 1)")
    parser.add_argument("--tags", metavar="TAG[,TAG]", default=None,
                        help="evaluate: only run cases carrying any of these tags")
    parser.add_argument("--eval-dir", metavar="PATH", default=None,
                        help="evaluate: directory holding fixtures/ and cases/ "
                             "(default: the repository evaluation/ directory)")
    parser.add_argument("--names", action="store_true",
                        help="profiles: print only profile names, one per line")
    return parser


def _classify_operands(parser: argparse.ArgumentParser, args) -> None:
    """Classify ordinary input operands and the two management commands."""
    operands = args.operands
    args.command = None
    args.input = None
    # NOTE: args.session_name is initialised by argparse (default None) and may
    # already hold a `--session NAME` value; it must not be reset here.
    if not operands:
        return
    if operands[0] == "doctor":
        if len(operands) != 1:
            parser.error("doctor does not accept additional operands")
        args.command = "doctor"
        return
    if operands[0] == "models":
        if len(operands) != 1:
            parser.error("models does not accept additional operands")
        args.command = "models"
        return
    if operands[0] == "evaluate":
        args.command = "evaluate"
        args.eval_case_ids = list(operands[1:])
        return
    if operands[0] == "profiles":
        if len(operands) != 1:
            parser.error("profiles does not accept additional operands")
        args.command = "profiles"
        return
    if operands[0] == "config":
        if len(operands) != 2 or operands[1] not in ("show", "path", "validate"):
            parser.error("config requires one of: show, path, validate")
        args.command = "config"
        args.config_action = operands[1]
        return
    if operands[0] == "cache":
        if len(operands) != 2 or operands[1] not in ("status", "path", "clear"):
            parser.error("cache requires one of: status, path, clear")
        args.command = "cache"
        args.cache_action = operands[1]
        return
    if operands[0] == "completions":
        if len(operands) != 2 or operands[1] not in ("bash", "zsh", "fish"):
            parser.error("completions requires one of: bash, zsh, fish")
        args.command = "completions"
        args.completion_shell = operands[1]
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
            args.session_name = operands[2]
        elif action in ("end", "status"):
            if len(operands) > 3:
                parser.error(f"session {action} accepts at most one NAME")
            args.session_name = operands[2] if len(operands) == 3 else None
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
    """Load configuration, applying CLI-level engine overrides on top.

    Precedence is CLI -> environment -> project config -> user config ->
    built-in defaults. Every value overridden here is labelled with its
    source so `--dry-run` and `doctor` can report where it came from without
    printing the value of anything sensitive.
    """
    config = load_config()
    overrides: dict[str, object] = {}
    origins: dict[str, str] = {}

    def override(key: str, value: object) -> None:
        overrides[key] = value
        origins[f"engine.{key}"] = SOURCE_CLI

    if args.backend:
        override("backend", args.backend)
    if args.endpoint:
        override("endpoint", args.endpoint)
    elif args.backend and config.origin("engine.endpoint") == SOURCE_BUILTIN:
        # Choosing a backend without naming an endpoint should land on that
        # backend's documented loopback default rather than another backend's.
        default_endpoint = default_endpoint_for_backend(canonical_backend_name(args.backend))
        if default_endpoint:
            override("endpoint", default_endpoint)
    if args.model:
        override("model", args.model)
    if args.timeout_seconds:
        override("timeout_seconds", args.timeout_seconds)
    if args.retries is not None:
        override("retries", args.retries)
    if args.max_tokens is not None:
        override("max_tokens", args.max_tokens)
    if args.reasoning_effort is not None:
        override("reasoning_effort", args.reasoning_effort)
    if overrides:
        config = config.with_overrides(engine=overrides).with_origins(origins)
    if args.concurrency is not None:
        if not 1 <= args.concurrency <= max(1, MAX_CONCURRENCY):
            raise ConfigError(
                f"--concurrency must be between 1 and {max(1, MAX_CONCURRENCY)}, "
                f"got {args.concurrency}"
            )
        config = config.with_overrides(
            chunking={"concurrency": args.concurrency}
        ).with_origins({"chunking.concurrency": SOURCE_CLI})
    if args.cache:
        mode = args.cache.strip().lower()
        if mode not in ("off", "read", "write", "readwrite"):
            raise ConfigError(
                f"--cache must be one of: off, read, write, readwrite; got {args.cache!r}"
            )
        config = config.with_overrides(cache={"mode": mode}).with_origins(
            {"cache.mode": SOURCE_CLI}
        )
    return config


def _run_session(args, config: Config, diag: Diagnostics) -> int:
    """Session management commands. Session policy lives in the session package."""
    from summarizer.session import SessionManager, render_status

    root = (
        Path(args.notes_path).expanduser()
        if getattr(args, "notes_path", None)
        else Path(config.session.notes_dir).expanduser()
    )
    manager = SessionManager(
        root=root,
        diag=diag,
        max_age_hours=config.session.max_age_hours,
        digest_format=config.session.digest_format,
    )
    action = args.session_action

    if action == "start":
        info = manager.start(args.session_name)
        print(f"session {info.name!r} started (id {info.id})")
        print(f"  state file: {info.directory / 'state.json'}")
        print("  runs are recorded when this session is selected:")
        print(f"    --session {info.name}   |   export SUMMARIZER_SESSION={info.name}")
        print(f"  or automatically for runs from {info.cwd}")
        return EXIT_OK

    if action == "end":
        info = manager.end(args.session_name)
        digest = info.digest_path or info.directory / "digest.md"
        print(f"session {info.name!r} closed ({info.runs} run(s))")
        print(f"  digest: {digest}")
        return EXIT_OK

    if action == "status":
        infos = manager.status(args.session_name)
        if (args.format or "").lower() == "json":
            import json

            payload = {
                "schema": "summarizer.session.status.v1",
                "sessions": [info.to_json() for info in infos],
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(render_status(infos, max_age_hours=config.session.max_age_hours))
            print(f"sessions root: {root}")
        return EXIT_OK

    raise ValueError(f"unknown session action {action!r}")


def _run_update_check(config: Config, diag: Diagnostics) -> int:
    from summarizer.update import UpdateManager

    manager = UpdateManager(config, diag=diag)
    report = manager.check()
    print(report.render())
    return EXIT_OK


def _print_stats(result, *, stream) -> None:
    stats = getattr(result, "stats", None) or {}
    if not stats:
        return
    lines = ["Stats:"]
    lines.append(
        f"  input:  {stats.get('input_bytes', 0)} bytes "
        f"(~{stats.get('input_tokens_est', 0)} tokens, {stats.get('input_chars', 0)} chars)"
    )
    lines.append(f"  chunks: {stats.get('chunks', 0)} (concurrency {stats.get('concurrency', 1)})")
    if stats.get("cached_chunks"):
        lines.append(f"  reused: {stats['cached_chunks']} cached chunk(s)")
    for key, label in (
        ("map_seconds", "map"),
        ("reduce_seconds", "reduce"),
        ("generation_seconds", "generation"),
        ("total_seconds", "total"),
    ):
        value = stats.get(key)
        if isinstance(value, (int, float)):
            lines.append(f"  {label}: {value:.3f}s")
    if stats.get("generated_tokens_est") is not None:
        lines.append(f"  generated: ~{stats['generated_tokens_est']} tokens (estimated)")
    if stats.get("tokens_per_second_est"):
        lines.append(f"  throughput: ~{stats['tokens_per_second_est']} tok/s (estimate)")
    usage = getattr(getattr(result, "engine", None), "last_usage", None)
    if usage and usage.get("completion_tokens") is not None:
        lines.append(
            f"  backend-reported output tokens (last request): {usage['completion_tokens']}"
        )
    stream.write("\n".join(lines) + "\n")
    stream.flush()


def _run_summarize(args, config: Config, diag: Diagnostics, *, cancel: CancelToken | None = None) -> int:
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
        stats=bool(args.stats),
    )
    reporter = build_progress(
        diag, enabled=config.defaults.progress and not args.no_progress
    )
    pipeline = Pipeline(config, diag=diag, cancel=cancel, progress=reporter)
    will_stream = opts.stream and fmt != "json"

    # Session selection happens before the run: an explicit selection that
    # cannot be honoured fails fast, instead of costing a model call.
    from summarizer.session import select_session

    session = None
    if not args.dry_run:
        session = select_session(
            root=(
                Path(args.notes_path).expanduser()
                if args.notes_path
                else Path(config.session.notes_dir).expanduser()
            ),
            max_age_hours=config.session.max_age_hours,
            digest_format=config.session.digest_format,
            enabled=config.session.enabled,
            explicit=args.session_name,
            no_session=args.no_session,
            diag=diag,
        )

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

    if args.stats:
        _print_stats(result, stream=sys.stderr)

    if session is not None:
        from summarizer.session import record_selected

        if cancel is not None:
            cancel.raise_if_cancelled()
        record_selected(
            session=session,
            result=result,
            digest_format=config.session.digest_format,
            diag=diag,
        )
    return EXIT_OK


def _install_sigint(cancel: CancelToken):
    """Cancel the run on Ctrl-C and keep the existing KeyboardInterrupt flow."""

    def handler(signum, frame):  # noqa: ARG001 - signal API
        cancel.cancel()
        raise KeyboardInterrupt

    try:
        return signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError, AttributeError):  # not the main thread
        return None


def _quiet_broken_pipe() -> None:
    """Point stdout at /dev/null so interpreter shutdown cannot complain."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    except Exception:
        pass
    try:
        sys.stdout.close()
    except Exception:
        pass


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
    if args.concurrency is not None and not 1 <= args.concurrency <= max(1, MAX_CONCURRENCY):
        diag.error(f"--concurrency must be between 1 and {max(1, MAX_CONCURRENCY)}")
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

    cancel = CancelToken()
    previous_sigint = _install_sigint(cancel)
    try:
        if args.command == "doctor":
            from summarizer.doctor import run_doctor

            return run_doctor(config, diag=diag)
        if args.command == "models":
            from summarizer.doctor import run_models

            return run_models(
                config, diag=diag, as_json=(args.format or "").lower() == "json"
            )
        if args.command == "profiles":
            from summarizer.introspect import run_profiles

            return run_profiles(args, config, diag)
        if args.command == "config":
            from summarizer.introspect import run_config

            return run_config(args, config, diag)
        if args.command == "completions":
            from summarizer.introspect import run_completions

            return run_completions(args, config, diag)
        if args.command == "cache":
            from summarizer.cache.cli import run_cache

            return run_cache(args, config, diag)
        if args.command == "evaluate":
            from summarizer.evaluation.cli import run_evaluate

            return run_evaluate(args, config, diag)
        if args.command == "session":
            return _run_session(args, config, diag)
        if args.check_update:
            return _run_update_check(config, diag)
        return _run_summarize(args, config, diag, cancel=cancel)
    except SummarizerError as exc:
        _print_error(diag, exc)
        if args.debug:
            traceback.print_exc()
        return exc.exit_code
    except KeyboardInterrupt:
        diag.error("interrupted")
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        # A downstream consumer (e.g. `head`) closed the pipe; exit quietly
        # without a traceback and without a shutdown-time flush error.
        _quiet_broken_pipe()
        return 141  # strict SIGPIPE convention
    except Exception as exc:
        diag.error(f"unexpected error: {type(exc).__name__}: {exc}")
        diag.info("hint: run with --debug for a traceback, and please report this")
        if args.debug:
            traceback.print_exc()
        return EXIT_INPUT
    finally:
        if previous_sigint is not None:
            try:
                signal.signal(signal.SIGINT, previous_sigint)
            except Exception:  # pragma: no cover
                pass


if __name__ == "__main__":
    raise SystemExit(main())
