"""A minimal, standard-library HTTP/1.1 client for local model servers.

Kept deliberately small: no frameworks, no HTTPX, no plugins. Supports JSON
requests and server-sent-event (SSE) streaming used by OpenAI-compatible
chat endpoints. All transport failures are mapped to typed exceptions so the
engine layer can distinguish:

    server unreachable / timeout / connection dropped mid-request /
    missing model / malformed response / unsupported capability
"""

from __future__ import annotations

import http.client
import json
import socket
import time
from typing import Any, BinaryIO, Iterator
from urllib.parse import urlsplit

from summarizer.errors import (
    CapabilityNotSupported,
    EngineConnectionFailure,
    EngineError,
    EngineTimeout,
    EngineUnreachable,
    Interrupted,
    MalformedResponse,
    ModelNotFound,
)
from summarizer.log import Diagnostics

__all__ = ["HttpClient", "HttpStatusError", "HttpStatusErrorKinds"]

_DEFAULT_CONNECT_TIMEOUT = 5.0

# While waiting for body/stream data we wake up at least this often so a
# cancellation request is observed promptly without busy-waiting.
_POLL_INTERVAL = 0.5


class HttpStatusError(Exception):
    """A non-2xx HTTP status with the server's error body attached."""

    def __init__(self, status: int, body: str, *, path: str) -> None:
        super().__init__(f"HTTP {status} from {path}: {body[:300]}")
        self.status = status
        self.body = body
        self.path = path


class HttpClient:
    def __init__(
        self,
        endpoint: str,
        *,
        timeout: float = 60.0,
        connect_timeout: float | None = None,
        diag: Diagnostics | None = None,
        max_response_bytes: int = 0,
    ) -> None:
        parsed = urlsplit(endpoint if "://" in endpoint else f"http://{endpoint}")
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"unsupported endpoint scheme in {endpoint!r}")
        self.scheme = parsed.scheme
        self.host = parsed.hostname or ""
        self.port = parsed.port or (443 if self.scheme == "https" else 80)
        self.base_path = (parsed.path or "").rstrip("/")
        self.timeout = timeout
        self.connect_timeout = connect_timeout if connect_timeout is not None else min(timeout, _DEFAULT_CONNECT_TIMEOUT)
        # Memory bound for response bodies and SSE event buffers. 0 disables.
        self.max_response_bytes = max(0, int(max_response_bytes))
        self.diag = diag
        # Optional cooperative cancellation token (see summarizer.cancel).
        self.cancel: object | None = None

    def url_for(self, path: str) -> str:
        return f"{self.scheme}://{self.host}:{self.port}{self.base_path}{path}"

    # -- low level -----------------------------------------------------------

    def _new_connection(self) -> http.client.HTTPConnection:
        if self.scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                self.host, self.port, timeout=self.connect_timeout
            )
        else:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=self.connect_timeout)
        return conn

    @staticmethod
    def _send(
        conn: http.client.HTTPConnection,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        timeout: float,
    ) -> http.client.HTTPResponse:
        body: bytes | None = None
        headers: dict[str, str] = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        deadline = time.monotonic() + timeout
        try:
            # HTTPConnection has a timeout *attribute* but no settimeout()
            # method. It is constructed with the short connect timeout; once
            # request() has connected and sent the body, move the underlying
            # socket to the full request deadline before waiting for headers.
            # Without this, a 180-second generation still times out after the
            # default 5-second connect timeout.
            conn.request(method, path, body=body, headers=headers)
            _set_socket_deadline(conn, deadline)
            return conn.getresponse()
        except socket.timeout as exc:
            raise EngineTimeout(f"timed out after {timeout:.0f}s talking to {path}") from exc
        except TimeoutError as exc:
            raise EngineTimeout(f"timed out after {timeout:.0f}s talking to {path}") from exc
        except ConnectionRefusedError as exc:
            raise EngineUnreachable(
                f"connection refused on {path}",
                hint="is the local model server running and is the endpoint right?",
            ) from exc
        except socket.gaierror as exc:
            raise EngineUnreachable(
                f"cannot resolve host for {path}: {exc}",
                hint="check the endpoint hostname in config",
            ) from exc
        except (BrokenPipeError, http.client.HTTPException, ConnectionResetError) as exc:
            raise EngineConnectionFailure(f"connection to {path} failed mid-request: {exc}") from exc

    def _read_body(
        self,
        resp: http.client.HTTPResponse,
        *,
        timeout: float,
        deadline: float,
        conn: http.client.HTTPConnection,
        path: str = "",
    ) -> bytes:
        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                if self._cancelled():
                    raise Interrupted("interrupted")
                if deadline - time.monotonic() <= 0:
                    raise EngineTimeout("request timed out while reading the response body")
                if getattr(resp, "fp", None) is None:
                    break
                _set_socket_deadline(conn, deadline)
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if self.max_response_bytes and total > self.max_response_bytes:
                    raise MalformedResponse(
                        f"response from {path or 'endpoint'} exceeded "
                        f"{self.max_response_bytes} bytes before it completed",
                        hint="raise [engine] max_response_bytes if your local "
                             "server legitimately returns larger payloads",
                    )
                chunks.append(chunk)
            return b"".join(chunks)
        except socket.timeout as exc:
            raise EngineTimeout("request timed out while reading the response body") from exc
        except TimeoutError as exc:
            raise EngineTimeout("request timed out while reading the response body") from exc
        except (http.client.IncompleteRead, ConnectionResetError, http.client.HTTPException) as exc:
            raise EngineConnectionFailure(f"connection dropped while reading the response: {exc}") from exc

    def _cancelled(self) -> bool:
        return bool(getattr(self.cancel, "cancelled", False))

    # -- request helpers -----------------------------------------------------

    def _do(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        timeout: float | None = None,
    ) -> tuple[http.client.HTTPResponse, bytes]:
        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        conn = self._new_connection()
        try:
            resp = self._send(conn, method, path, payload, timeout=timeout)
            body = self._read_body(resp, timeout=timeout, deadline=deadline, conn=conn, path=path)
            if resp.status not in (200, 201):
                raise HttpStatusError(resp.status, body.decode("utf-8", "replace"), path=path)
            content_type = resp.getheader("Content-Type", "")
            if "json" in content_type:
                try:
                    return resp, json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise MalformedResponse(
                        f"endpoint returned non-JSON for {path}: {exc}",
                        hint=f"body starts: {body[:160]!r}",
                    ) from exc
            return resp, body
        finally:
            conn.close()

    def get_json(self, path: str, *, timeout: float | None = None) -> Any:
        _, data = self._do("GET", path, None, timeout=timeout)
        return data

    def post_json(self, path: str, payload: dict[str, Any], *, timeout: float | None = None) -> Any:
        _, data = self._do("POST", path, payload, timeout=timeout)
        return data

    def stream_sse(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Iterator[dict[str, Any]]:
        """POST and yield parsed JSON events from a Server-Sent-Events reply."""
        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        conn = self._new_connection()
        resp = self._send(conn, "POST", path, payload, timeout=timeout)
        try:
            if resp.status not in (200, 201):
                body = self._read_body(resp, timeout=timeout, deadline=deadline, conn=conn, path=path)
                raise HttpStatusError(resp.status, body.decode("utf-8", "replace"), path=path)
            # SSE events arrive delimiter-to-delimiter; a malicious or broken
            # server can withhold the blank line forever. Bound the buffer so
            # no single unterminated event can exhaust memory (>= the response
            # cap when one is configured; 1 MiB otherwise).
            buffer = ""
            buffer_limit = max(self.max_response_bytes, 1_048_576)
            while True:
                if self._cancelled():
                    raise Interrupted("interrupted")
                if deadline - time.monotonic() <= 0:
                    raise EngineTimeout("stream timed out before completion")
                _set_socket_timeout(
                    conn, min(_POLL_INTERVAL, max(0.1, deadline - time.monotonic())), resp
                )
                try:
                    # read1 returns whatever is available without waiting to
                    # fill a buffer, so a poll timeout loses no partial event.
                    data = resp.read1(65536) if hasattr(resp, "read1") else resp.readline()
                except (socket.timeout, TimeoutError):
                    continue
                if not data:
                    break
                buffer += data.decode("utf-8", "replace") if isinstance(data, bytes) else data
                if len(buffer) > buffer_limit:
                    raise MalformedResponse(
                        f"streamed event from {path} exceeded {buffer_limit} bytes without a delimiter",
                        hint="the local server is not emitting valid SSE framing",
                    )
                while "\n\n" in buffer or "\r\n\r\n" in buffer:
                    if "\r\n\r\n" in buffer:
                        event, buffer = buffer.split("\r\n\r\n", 1)
                    else:
                        event, buffer = buffer.split("\n\n", 1)
                    parsed = _parse_sse_event(event)
                    if parsed is not None:
                        if parsed == "DONE":
                            return
                        yield parsed
        except socket.timeout as exc:
            raise EngineTimeout("stream timed out before completion") from exc
        except TimeoutError as exc:
            raise EngineTimeout("stream timed out before completion") from exc
        except (http.client.IncompleteRead, ConnectionResetError, http.client.HTTPException) as exc:
            raise EngineConnectionFailure(f"connection dropped during streaming: {exc}") from exc
        finally:
            conn.close()


def _parse_sse_event(block: str) -> dict[str, Any] | str | None:
    data_lines: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
    if not data_lines:
        return None
    data = "\n".join(data_lines)
    if data.strip() == "[DONE]":
        return "DONE"
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def _set_socket_deadline(conn: http.client.HTTPConnection, deadline: float) -> None:
    """Apply the remaining request budget after a connection is established."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EngineTimeout("request timed out before the server responded")
    sock = getattr(conn, "sock", None)
    if sock is not None:
        sock.settimeout(max(0.1, remaining))


def _set_socket_timeout(conn: http.client.HTTPConnection, seconds: float, resp=None) -> None:
    """Set a short poll timeout without treating it as a request failure.

    ``http.client`` may clear ``conn.sock`` once a response begins
    (notably for connection-close framing), so fall back to the socket owned
    by the response's buffered reader.
    """
    sock = getattr(conn, "sock", None)
    if sock is None and resp is not None:
        raw = getattr(getattr(resp, "fp", None), "raw", None)
        sock = getattr(raw, "_sock", None)
    if sock is not None:
        sock.settimeout(max(0.05, seconds))


class HttpStatusErrorKinds:
    """Heuristics for turning 4xx status bodies into typed engine errors."""

    @staticmethod
    def capability_refused(exc: HttpStatusError) -> CapabilityNotSupported | None:
        message = exc.body.lower()
        if exc.status in (400, 422) and any(
            token in message
            for token in ("response_format", "json_object", "unsupported parameter", "format")
        ):
            return CapabilityNotSupported(
                f"endpoint rejected the request: {exc.body[:200]}",
                hint="the model/backend does not support the requested output format",
            )
        return None

    @staticmethod
    def model_missing(exc: HttpStatusError) -> ModelNotFound | None:
        message = exc.body.lower()
        if "model" in message and "not found" in message:
            return ModelNotFound(
                f"the model is not available on the endpoint: {exc.body[:200]}",
                hint="is the model pulled/loaded on the server? (`ollama run <model>`)",
            )
        return None
