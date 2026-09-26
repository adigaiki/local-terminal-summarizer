"""`summarize doctor` and `summarize models`: local-environment diagnostics.

The doctor checks configuration layers, engine reachability, the configured
model, model discovery, and capabilities, with actionable hints. It makes only
the local engine requests needed for those checks and never contacts the
internet, downloads anything, or selects a model on the user's behalf.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from summarizer import __version__
from summarizer.cache.store import LocalCache
from summarizer.config import (
    SOURCE_BUILTIN,
    Config,
    project_config_requested,
    user_config_path,
)
from summarizer.engine import Engine, EngineCapabilities, create_engine
from summarizer.engine.capabilities import DEFAULT_CONTEXT_LENGTH
from summarizer.errors import EngineError, ModelNotFound, SummarizerError
from summarizer.log import Diagnostics, redact_url

__all__ = [
    "DoctorReport",
    "Check",
    "diagnose",
    "render_doctor",
    "run_doctor",
    "render_models",
    "list_local_models",
]

#: How many discovered model names to print before eliding the rest.
MAX_LISTED_MODELS = 40


@dataclass
class Check:
    name: str
    ok: bool
    message: str = ""
    hint: str = ""
    detail: list[str] = field(default_factory=list)
    # An intentionally-uninstalled extra is reported, never treated as a
    # failure: optional readers must not make the doctor look broken.
    optional: bool = False


@dataclass
class DoctorReport:
    checks: list[Check]
    engine: Engine
    model: str
    endpoint: str
    backend: str
    config: Config
    capabilities: EngineCapabilities | None = None
    origins: dict[str, str] = field(default_factory=dict)

    @property
    def all_ok(self) -> bool:
        return all(check.ok for check in self.checks if not check.optional)


def _check(
    name: str,
    ok: bool,
    message: str = "",
    hint: str = "",
    detail: list[str] | None = None,
    optional: bool = False,
) -> Check:
    return Check(name=name, ok=ok, message=message, hint=hint, detail=detail or [], optional=optional)


def _reader_checks() -> list[Check]:
    """Diagnose optional reader capabilities locally (no downloads, no installs)."""
    checks: list[Check] = []

    # PDF text extraction (optional `pdf` extra).
    if importlib.util.find_spec("pypdf") is not None:
        checks.append(_check("PDF reader", True, message="installed (pypdf)"))
    else:
        checks.append(
            _check(
                "PDF reader",
                False,
                message="optional, not installed",
                hint="install with: pip install 'summarizer[pdf]'",
                optional=True,
            )
        )

    # OCR (optional `ocr` extra plus local binaries).
    python_ok = all(
        importlib.util.find_spec(module) is not None
        for module in ("pytesseract", "pdf2image")
    )
    missing_binaries = [
        binary for binary in ("pdftoppm", "pdfinfo", "tesseract")
        if shutil.which(binary) is None
    ]
    if python_ok and not missing_binaries:
        checks.append(_check("OCR", True, message="installed (local Tesseract + Poppler)"))
    else:
        missing = list(missing_binaries)
        if not python_ok:
            missing.insert(0, "pytesseract/pdf2image")
        checks.append(
            _check(
                "OCR",
                False,
                message=f"optional, not installed (missing: {', '.join(missing)})",
                hint="pip install 'summarizer[ocr]' plus local Tesseract and "
                     "Poppler; nothing is installed automatically and no "
                     "online OCR service is used",
                optional=True,
            )
        )
    return checks


def _configuration_check(config: Config) -> Check:
    """Report which layers exist and which supplied the engine settings.

    Only labels and non-secret values are shown. No environment variable
    *value* is ever printed.
    """
    user = config.user_config_path or user_config_path()
    project = config.project_config_path
    requested = project_config_requested()

    detail: list[str] = [
        "precedence: command line > environment > project config > user config > built-in defaults",
    ]
    if config.user_config_path and config.user_config_path.is_file():
        detail.append(f"user config: {config.user_config_path}")
    else:
        detail.append(f"user config: none at {user}")
    if project is not None:
        detail.append(f"project config: {project} (opt-in)")
    elif requested is not None:
        detail.append(f"project config: {requested} (opted in but not present)")
    else:
        detail.append(
            "project config: disabled (set SUMMARIZER_PROJECT_CONFIG=1 to opt in)"
        )
    for key in ("engine.backend", "engine.model", "engine.endpoint", "engine.reasoning_effort"):
        detail.append(f"{key}: {config.origin(key)}")

    source = (
        "built-in defaults"
        if config.user_config_path is None and config.project_config_path is None
        else "config files + environment"
    )
    return _check("Configuration", True, f"loaded from {source}", detail=detail)


def diagnose(
    config: Config,
    *,
    diag: Diagnostics,
    engine: Engine | None = None,
) -> DoctorReport:
    checks: list[Check] = []

    # 1. Configuration
    checks.append(_configuration_check(config))

    # 2. Installation health (no network, no engine needed)
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    install_detail = [f"python: {python_version}", f"summarizer: {__version__}"]
    executable = shutil.which("summarize")
    if executable:
        install_detail.append(f"executable: {executable}")
    checks.append(
        _check(
            "Installation",
            True,
            message=f"summarizer {__version__} on Python {python_version}",
            detail=install_detail,
        )
    )

    # 3. Cache / checkpoint configuration (read-only; never creates anything)
    cache_status = LocalCache(config.cache, diag=diag).status()
    if config.cache.mode == "off":
        checks.append(
            _check(
                "Cache",
                True,
                message="off (no local state is written)",
                detail=[f"would live at {cache_status.path}"],
            )
        )
    else:
        checks.append(
            _check(
                "Cache",
                True,
                message=f"{cache_status.mode}; {cache_status.entries} entries, "
                        f"{cache_status.bytes} bytes",
                detail=[f"path: {cache_status.path}", "clear with: summarize cache clear"],
            )
        )

    # 4. Engine
    if engine is None:
        engine = create_engine(config, diag=diag)

    # 5. Reachability
    reachable = False
    try:
        reachable = bool(engine.health())
    except Exception:
        reachable = False
    checks.append(
        _check(
            "Backend reachable",
            reachable,
            detail=[f"endpoint: {redact_url(engine.endpoint)}"],
        )
    )
    if not checks[-1].ok:
        checks[-1].hint = "start your local model server, or fix `[engine] endpoint` in config"
        checks.append(_check("Model available", False, message="skipped: backend unreachable"))
        checks.append(_check("Capabilities", False, message="skipped: backend unreachable"))
        checks.extend(_reader_checks())
        return DoctorReport(
            checks=checks, engine=engine, model=engine.model,
            endpoint=engine.endpoint, backend=engine.backend, config=config,
            origins=dict(config.origins),
        )

    # 4. Model availability + discovery
    models: list[str] = []
    try:
        models = engine.list_models()
        if models:
            available = _model_is_available(engine.model, models)
            listed = ", ".join(models[:MAX_LISTED_MODELS])
            if len(models) > MAX_LISTED_MODELS:
                listed += f", ... (+{len(models) - MAX_LISTED_MODELS} more)"
            if available:
                checks.append(
                    _check(
                        "Model available",
                        True,
                        message=f"configured model {engine.model!r} is installed",
                        detail=[f"{len(models)} model(s) on this backend: {listed}"],
                    )
                )
            else:
                checks.append(
                    _check(
                        "Model available",
                        False,
                        message=f"configured model {engine.model!r} is not installed",
                        hint=f"set `[engine] model` to an installed model or pull it; available: {listed}",
                        detail=[f"{len(models)} model(s) on this backend: {listed}"],
                    )
                )
        else:
            checks.append(
                _check(
                    "Model availability",
                    False,
                    message="the backend reported no models",
                    hint="the backend may not support model listing; set "
                         "`[engine] model` explicitly",
                )
            )
    except ModelNotFound as exc:
        checks.append(_check("Model unavailable", False, message=exc.message, hint=exc.hint or "model not found on the server"))
    except EngineError as exc:
        checks.append(
            _check(
                "Model availability check",
                False,
                message=f"server answered, but: {exc.message}",
                hint=exc.hint or "check the server logs",
            )
        )

    # 5. Capabilities (negotiation vocabulary, not backend-name guessing)
    capabilities: EngineCapabilities | None = None
    try:
        capabilities = engine.capabilities()
        checks.append(
            _check(
                "Streaming supported",
                capabilities.supports_streaming,
                message="yes" if capabilities.supports_streaming else "no",
            )
        )
        if capabilities.supports_reasoning_control is True:
            reasoning_state = "supported by backend"
        elif capabilities.supports_reasoning_control is False:
            reasoning_state = "not supported by backend"
        else:
            reasoning_state = "unknown; omitted to preserve compatibility"
        checks.append(
            _check(
                "Reasoning control",
                capabilities.supports_reasoning_control is not False,
                message=reasoning_state,
            )
        )
        ctx = capabilities.context_length or DEFAULT_CONTEXT_LENGTH
        checks.append(
            _check(
                "Context window known",
                bool(capabilities.context_length),
                message=(
                    f"{ctx} tokens ({capabilities.effective_context_source})"
                    if capabilities.context_is_known
                    else f"unknown; assuming {DEFAULT_CONTEXT_LENGTH} (fallback)"
                ),
                detail=[f"used for chunk sizing: ~{ctx} tokens"],
            )
        )
        json_state = (
            "reported by backend"
            if capabilities.supports_structured_output is True
            else "unavailable on this backend"
            if capabilities.supports_structured_output is False
            else "unknown (probed at request time)"
        )
        checks.append(
            _check(
                "Structured JSON",
                capabilities.supports_structured_output is not False,
                message=json_state,
            )
        )
        checks.append(
            _check(
                "Model listing",
                capabilities.supports_model_listing,
                message="supported" if capabilities.supports_model_listing else "not supported",
            )
        )
        if capabilities.notes:
            checks.append(_check("Capability notes", True, detail=list(capabilities.notes)))
    except EngineError as exc:
        checks.append(_check("Capabilities", False, message=exc.message, hint=exc.hint or "the server may not expose capability info"))

    checks.extend(_reader_checks())
    return DoctorReport(
        checks=checks, engine=engine, model=engine.model, endpoint=engine.endpoint,
        backend=engine.backend, config=config, capabilities=capabilities,
        origins=dict(config.origins),
    )


def _model_is_available(configured: str, installed: list[str]) -> bool:
    """Match exact model tags, with Ollama's conventional ``:latest`` alias.

    Prefix matching incorrectly reports e.g. ``model:8b`` available when only
    ``model:8b-instruct`` exists. A tagless configured name is allowed to
    match an installed tag of the same model family, as Ollama resolves that
    conventional shorthand itself.
    """
    requested = configured.removesuffix(":latest")
    normalized = [name.removesuffix(":latest") for name in installed]
    if requested in normalized:
        return True
    return ":" not in requested and any(name.split(":", 1)[0] == requested for name in normalized)


def render_doctor(report: DoctorReport) -> str:
    lines: list[str] = ["Summarizer diagnostics", ""]
    for check in report.checks:
        glyph = "\u2713" if check.ok else ("!" if check.optional else "\u2717")
        line = f"{glyph} {check.name}"
        if check.message:
            line += f" — {check.message}"
        lines.append(line)
        for detail in check.detail:
            lines.append(f"    {detail}")
        if not check.ok and check.hint:
            lines.append(f"    hint: {check.hint}")
    lines += [
        "",
        "Backend:",
        f"  backend:  {report.backend}",
        f"  model:    {report.model}",
        f"  endpoint: {redact_url(report.endpoint)}",
    ]
    caps = report.capabilities
    if caps is not None:
        lines.append(f"  caps:     {caps.describe()}")
    lines += ["", ""]
    if report.all_ok:
        lines.append("Everything looks good.")
    else:
        lines.append("Some checks failed. See the hints above.")
    return "\n".join(lines)


def run_doctor(
    config: Config,
    *,
    diag: Diagnostics,
    engine: Engine | None = None,
) -> int:
    """Run the doctor and print the report. Returns a process exit code.

    Also creates the config when possible; ConfigError from the loader is
    reported separately (exit 3) by the CLI.
    """
    report = diagnose(config, diag=diag, engine=engine)
    print(render_doctor(report))
    return 0 if report.all_ok else 2


# --- model discovery --------------------------------------------------------


def list_local_models(
    engine: Engine,
    *,
    diag: Diagnostics,
) -> list[str]:
    """Ask the backend for its installed models.

    Never downloads and never guesses. Raises an EngineError (or returns an
    empty list when the backend advertises no model listing) so callers can
    report honestly.
    """
    try:
        caps = engine.capabilities()
    except EngineError:
        caps = None
    if caps is not None and not caps.supports_model_listing:
        return []
    return engine.list_models()


def render_models(model: str, models: list[str], *, backend: str, endpoint: str) -> str:
    lines = [
        f"Local models on {backend} ({redact_url(endpoint)}):",
        "",
    ]
    if not models:
        lines.append("  (none reported; the backend may not support model listing)")
    else:
        for name in models:
            marker = "*" if _model_is_available(name, [model]) else " "
            lines.append(f"  {marker} {name}")
        lines.append("")
        lines.append(f"* = configured model ({model!r})")
    return "\n".join(lines)


def run_models(
    config: Config,
    *,
    diag: Diagnostics,
    engine: Engine | None = None,
    as_json: bool = False,
) -> int:
    """`summarize models`: list models the configured local backend reports."""
    if engine is None:
        engine = create_engine(config, diag=diag)
    try:
        models = list_local_models(engine, diag=diag)
    except ModelNotFound as exc:
        diag.error(exc.message)
        return exc.exit_code
    except SummarizerError as exc:
        diag.error(exc.message)
        if exc.hint:
            diag.info(f"hint: {exc.hint}")
        return exc.exit_code
    except Exception as exc:  # transport-level failure, reported plainly
        diag.error(f"could not list models: {type(exc).__name__}: {exc}")
        return 2

    if as_json:
        payload = {
            "schema": "summarizer.models.v1",
            "backend": engine.backend,
            "endpoint": redact_url(engine.endpoint),
            "configured_model": engine.model,
            "configured_model_available": _model_is_available(engine.model, models),
            "models": models,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_models(engine.model, models, backend=engine.backend, endpoint=engine.endpoint))
    return 0
