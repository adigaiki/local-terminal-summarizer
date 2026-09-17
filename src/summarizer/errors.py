"""Exception hierarchy for summarizer.

Exit codes (stable, documented contract):

    0  success
    1  input error
    2  engine error
    3  config error
    4  update error
    130 interrupted by SIGINT (conventional)
"""

from __future__ import annotations

__all__ = [
    "SummarizerError",
    "InputError",
    "EngineError",
    "ConfigError",
    "UpdateError",
    "EngineUnreachable",
    "ModelNotFound",
    "MalformedResponse",
    "EngineTimeout",
    "EngineConnectionFailure",
    "CapabilityNotSupported",
    "Interrupted",
]


class SummarizerError(Exception):
    """Base class for all errors with stable exit codes."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class InputError(SummarizerError):
    """Bad input: missing input, unreadable files, oversized input."""

    exit_code = 1


class EngineError(SummarizerError):
    """Any failure in the LLM engine layer."""

    exit_code = 2


class ConfigError(SummarizerError):
    """Invalid or unreadable configuration."""

    exit_code = 3


class UpdateError(SummarizerError):
    """Errors in the (opt-in) update-checking machinery."""

    exit_code = 4


# --- Engine-specific errors -------------------------------------------------
#
# The engine layer must distinguish these failure modes; they must not all be
# collapsed into one generic error.


class EngineUnreachable(EngineError):
    """The endpoint could not be reached at all (DNS, refused, unreachable)."""


class ModelNotFound(EngineError):
    """The endpoint answered, but the requested model does not exist there."""


class MalformedResponse(EngineError):
    """The endpoint answered with something we could not parse."""


class EngineTimeout(EngineError):
    """The endpoint did not answer within the configured timeout."""


class EngineConnectionFailure(EngineError):
    """Connection dropped mid-request (reset, incomplete read, EOF)."""


class CapabilityNotSupported(EngineError):
    """The backend or model does not support a required capability."""


class Interrupted(SummarizerError):
    """Raised when the user interrupts (Ctrl-C) during processing."""

    exit_code = 130