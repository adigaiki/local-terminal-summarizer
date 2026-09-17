"""`summarize doctor`: friendly local-environment diagnostics.

The doctor checks configuration, engine reachability, configured model,
model availability, and capabilities, with actionable hints. It makes only
the local engine requests needed for those checks and never contacts the
internet.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass, field
from typing import Callable

from summarizer.config import Config, user_config_path
from summarizer.engine import Engine, EngineCapabilities, create_engine
from summarizer.engine.capabilities import DEFAULT_CONTEXT_LENGTH
from summarizer.errors import EngineError, ModelNotFound, SummarizerError
from summarizer.log import Diagnostics

__all__ = ["DoctorReport", "Check", "diagnose", "render_doctor", "run_doctor"]


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


def diagnose(
    config: Config,
    *,
    diag: Diagnostics,
    engine: Engine | None = None,
) -> DoctorReport:
    checks: list[Check] = []

    # 1. Configuration
    cfg_source = "built-in defaults"
    if config.user_config_path and config.user_config_path.is_file():
        cfg_source = str(config.user_config_path)
    elif config.project_config_path and config.project_config_path.is_file():
        cfg_source = str(config.project_config_path)
    checks.append(_check("Configuration", True, f"loaded from {cfg_source}"))

    # 2. Engine
    if engine is None:
        engine = create_engine(config, diag=diag)

    # 3. Reachability
    reachable = False
    try:
        reachable = bool(engine.health())
    except Exception:
        reachable = False
    checks.append(_check("Engine reachable", reachable, detail=[f"endpoint: {engine.endpoint}"]))
    if not checks[-1].ok:
        checks[-1].hint = "start your local model server, or fix `[engine] endpoint` in config"
        checks.append(_check("Model available", False, message="skipped: engine unreachable"))
        checks.append(_check("Capabilities", False, message="skipped: engine unreachable"))
        checks.extend(_reader_checks())
        return DoctorReport(
            checks=checks, engine=engine, model=engine.model,
            endpoint=engine.endpoint, backend=engine.backend, config=config,
        )

    # 4. Model availability
    models: list[str] = []
    try:
        models = engine.list_models()
        if models:
            available = _model_is_available(engine.model, models)
            if available:
                checks.append(_check("Model available", True, message=f"configured model {engine.model!r} is installed"))
            else:
                names = ", ".join(models[:8]) if models else "none reported"
                checks.append(
                    _check(
                        "Model available",
                        False,
                        message=f"configured model {engine.model!r} is not installed",
                        hint=f"set `[engine] model` to an installed model or pull it; available: {names}",
                    )
                )
        else:
            checks.append(
                _check(
                    "Model availability",
                    False,
                    hint="the server reported no models; is a model loaded?",
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

    # 5. Capabilities
    try:
        caps = engine.capabilities()
        checks.append(_check("Streaming supported", bool(caps.streaming)))
        reasoning_state = (
            "supported by backend"
            if caps.reasoning_control is True
            else "unknown; omitted to preserve compatibility"
            if caps.reasoning_control is None
            else "not supported by backend"
        )
        checks.append(
            _check(
                "Reasoning control",
                caps.reasoning_control is not False,
                message=reasoning_state,
            )
        )
        ctx = caps.context_length or DEFAULT_CONTEXT_LENGTH
        checks.append(_check("Context window known", bool(caps.context_length), detail=[f"~{ctx} tokens used for chunk sizing"]))
        json_state = (
            "reported by backend"
            if caps.structured_json is True
            else "unavailable on this backend" if caps.structured_json is False else "unknown (probed at request time)"
        )
        checks.append(_check("Structured JSON", caps.structured_json is not False, message=json_state))
    except EngineError as exc:
        checks.append(_check("Capabilities", False, message=exc.message, hint=exc.hint or "the server may not expose capability info"))

    checks.extend(_reader_checks())
    return DoctorReport(checks=checks, engine=engine, model=engine.model, endpoint=engine.endpoint, backend=engine.backend, config=config)


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
        "Engine:",
        f"  backend:  {report.backend}",
        f"  model:    {report.model}",
        f"  endpoint: {report.endpoint}",
        "",
    ]
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
