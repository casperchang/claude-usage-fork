"""Google AI Pro quota collector (opt-in second provider).

Talks to the locally-running Antigravity IDE's ``language_server`` over its
localhost Connect-RPC port, calling
``/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary``
— the same call the IDE's own quota panel makes. The response carries
``groups[].buckets[]``, each bucket a ``window`` ("5h" or "weekly") with a
``remainingFraction`` and an RFC3339 ``resetTime`` — the same
session/weekly pair as Claude, so the overlay renders them with the exact
same ring/bar primitives.

Note the payload reports *remaining* fraction while the widget draws
*used* percentage, so everything here converts with ``1 - remaining``.

Discovery is deliberately done fresh on every refresh: Antigravity picks a
random port and a new CSRF token each time it launches, so a cached
address goes stale the moment the user restarts the IDE. Finding the
process is cheap (one ``ps``); the RPC itself is not, so results are
cached on disk (``~/.cache/claude-usage/gemini_limits.json``) and only
refreshed every ``poll_seconds``. Between polls — and on RPC failure —
the cache is served, with expired windows clamped back to zero exactly
like the Claude sample-fallback path in ``collector.collect_all``.

When Antigravity is not running there is nothing to ask, so
``collect_gemini`` reports unavailable and the UI simply never shows the
Gemini rows.

POSIX-only for now: discovery shells out to ``ps`` and to ``ss``/``lsof``
to find the listening port, neither of which exists on Windows.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RPC_TIMEOUT_SECONDS = 8
DEFAULT_POLL_SECONDS = 300
CACHE_PATH = Path.home() / ".cache" / "claude-usage" / "gemini_limits.json"

RPC_PATH = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
# Antigravity inherits Codeium's header name for its CSRF check.
CSRF_HEADER = "x-codeium-csrf-token"
# Quota groups the server reports. "gemini" is the Google-model group
# (Gemini Pro/Flash); "3p" is the third-party group (Claude/GPT served
# through Antigravity) — a different pool that is still part of the same
# Google AI Pro subscription.
DEFAULT_GROUP = "gemini"

# The IDE serves the same handler on a plain-HTTP and a TLS port, and which
# one answers varies by build, so we try each until one does and remember
# the winner for the life of the process.
_last_good: tuple[str, int] | None = None


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    """Run *cmd* and return stdout, or "" if it fails in any way."""
    try:
        out = subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False,
        )
        return out.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def find_language_server() -> tuple[int, str] | None:
    """Return ``(pid, csrf_token)`` for a running Antigravity language_server.

    Returns None when Antigravity is not running, or when its command line
    carries no ``--csrf_token`` (older builds, or a differently-launched
    server we would not be able to authenticate against anyway).
    """
    # -ww stops ps truncating the command line at terminal width, which
    # would otherwise cut off the token we are looking for.
    for line in _run(["ps", "-ww", "-eo", "pid=,args="]).splitlines():
        if "language_server" not in line or "--csrf_token" not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        token = ""
        for i, arg in enumerate(parts):
            if arg == "--csrf_token" and i + 1 < len(parts):
                token = parts[i + 1]
            elif arg.startswith("--csrf_token="):
                token = arg.split("=", 1)[1]
        if token:
            return pid, token
    return None


def find_listening_ports(pid: int) -> list[int]:
    """Return the localhost TCP ports *pid* is listening on (may be empty)."""
    ports: list[int] = []

    # Linux: ss prints "users:((\"language_server\",pid=123,fd=19))".
    for line in _run(["ss", "-ltnp"]).splitlines():
        if f"pid={pid}," not in line:
            continue
        for match in re.findall(r"127\.0\.0\.1:(\d+)", line):
            ports.append(int(match))

    # macOS (and Linux without iproute2): lsof scoped to the one pid.
    if not ports:
        out = _run(["lsof", "-nP", "-a", "-p", str(pid),
                    "-iTCP", "-sTCP:LISTEN"])
        for match in re.findall(r"(?:127\.0\.0\.1|\[?::1\]?|\*):(\d+)", out):
            ports.append(int(match))

    # Deduplicate while keeping discovery order stable.
    return list(dict.fromkeys(ports))


def _post(scheme: str, port: int, token: str,
          timeout: float = RPC_TIMEOUT_SECONDS) -> Any:
    """POST an empty RetrieveUserQuotaSummary request; return parsed JSON or None."""
    url = f"{scheme}://127.0.0.1:{port}{RPC_PATH}"
    req = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", CSRF_HEADER: token},
    )
    # The IDE's TLS port serves a self-signed cert for localhost; there is
    # no trust decision to make about a socket we already located by pid.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None


def fetch_quota_summary(pid: int, token: str) -> Any:
    """Try every plausible (scheme, port) until one answers; None if none do."""
    global _last_good

    candidates: list[tuple[str, int]] = []
    if _last_good is not None:
        candidates.append(_last_good)
    for port in find_listening_ports(pid):
        for scheme in ("http", "https"):
            if (scheme, port) not in candidates:
                candidates.append((scheme, port))

    for scheme, port in candidates:
        payload = _post(scheme, port, token)
        # A Connect error body is a dict with "code"/"message" and no
        # "response" — treat only a payload we can parse as a hit.
        if parse_quota_summary(payload) is not None:
            _last_good = (scheme, port)
            return payload
    _last_good = None
    return None


def _parse_reset(value: Any) -> int:
    """Convert a bucket resetTime to unix seconds (0 when absent/unparseable).

    The Connect JSON mapping renders ``google.protobuf.Timestamp`` as an
    RFC3339 string, but tolerate a raw epoch too in case a future build
    switches encodings.
    """
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        ts = int(value)
        return ts // 1000 if ts > 10**12 else ts
    if not isinstance(value, str) or not value.strip():
        return 0
    text = value.strip()
    # Python's fromisoformat only learned "Z" in 3.11; normalise for 3.10.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Timestamps can carry nanosecond precision, which fromisoformat rejects.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def parse_quota_summary(payload: Any, group: str = DEFAULT_GROUP) -> dict[str, Any] | None:
    """Extract the 5h/weekly pair for *group* from a RetrieveUserQuotaSummary result.

    Returns ``{"session_pct", "session_reset", "weekly_pct", "weekly_reset"}``
    (pct 0..1 *used*, reset as unix seconds, 0 when absent), or None when the
    payload carries no usable bucket for that group.

    Buckets are matched on their ``bucketId`` prefix (``gemini-5h``,
    ``gemini-weekly``, ``3p-5h``, ``3p-weekly``) and fall back to the
    ``window`` field, so a build that renames one of the two still parses.
    """
    if not isinstance(payload, dict):
        return None
    # The local server wraps the upstream message in {"response": {...}}.
    body = payload.get("response")
    if not isinstance(body, dict):
        body = payload

    buckets: list[dict[str, Any]] = []
    for grp in body.get("groups") or []:
        if isinstance(grp, dict):
            buckets.extend(b for b in (grp.get("buckets") or [])
                           if isinstance(b, dict))
    buckets.extend(b for b in (body.get("buckets") or []) if isinstance(b, dict))
    if not buckets:
        return None

    prefix = f"{group}-"
    session: tuple[float, int] | None = None
    weekly: tuple[float, int] | None = None

    for bucket in buckets:
        bucket_id = str(bucket.get("bucketId") or "")
        if not bucket_id.startswith(prefix):
            continue
        remaining = bucket.get("remainingFraction")
        if remaining is None:
            # Some builds report a raw count instead of a fraction; without
            # a denominator there is no percentage to draw, so skip it.
            continue
        try:
            used = 1.0 - float(remaining)
        except (TypeError, ValueError):
            continue
        used = max(0.0, min(1.0, used))
        entry = (used, _parse_reset(bucket.get("resetTime")))

        window = str(bucket.get("window") or "").lower()
        suffix = bucket_id[len(prefix):].lower()
        if "5h" in (window, suffix) or "hour" in window:
            session = entry
        elif "weekly" in (window, suffix) or "week" in window:
            weekly = entry

    if session is None and weekly is None:
        return None
    return {
        "session_pct": session[0] if session else 0.0,
        "session_reset": session[1] if session else 0,
        "weekly_pct": weekly[0] if weekly else 0.0,
        "weekly_reset": weekly[1] if weekly else 0,
    }


def _load_cache() -> dict[str, Any] | None:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _save_cache(payload: Any) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": time.time(), "payload": payload}, fh)
        os.replace(tmp, CACHE_PATH)
    except OSError:
        pass


def _clamp_expired(parsed: dict[str, Any], now_ts: float) -> dict[str, Any]:
    """A window whose reset has passed has rolled over — show 0, not stale %."""
    out = dict(parsed)
    if out["session_reset"] and now_ts >= out["session_reset"]:
        out["session_pct"], out["session_reset"] = 0.0, 0
    if out["weekly_reset"] and now_ts >= out["weekly_reset"]:
        out["weekly_pct"], out["weekly_reset"] = 0.0, 0
    return out


def collect_gemini(poll_seconds: int = DEFAULT_POLL_SECONDS,
                   group: str = DEFAULT_GROUP) -> dict[str, Any]:
    """Return Google AI Pro utilization for the overlay; never raises.

    ``{"available": bool, "session_pct", "session_reset", "weekly_pct",
    "weekly_reset", "error": str}`` — available=False hides the Gemini UI.
    """
    unavailable = {
        "available": False, "error": "",
        "session_pct": 0.0, "session_reset": 0,
        "weekly_pct": 0.0, "weekly_reset": 0,
    }
    if os.name != "posix":
        return {**unavailable, "error": "gemini provider is POSIX-only for now"}

    now_ts = time.time()
    cache = _load_cache()
    if cache is not None:
        age = now_ts - float(cache.get("fetched_at", 0) or 0)
        parsed = parse_quota_summary(cache.get("payload"), group)
        if parsed is not None and 0 <= age < poll_seconds:
            return {"available": True, "error": "", **_clamp_expired(parsed, now_ts)}

    # Antigravity re-rolls its port and CSRF token on every launch, so the
    # address is rediscovered here rather than cached.
    found = find_language_server()
    if found is None:
        # Serve a recent cache so closing the IDE doesn't blank the rows
        # mid-window; give up once the data is older than the window itself.
        if cache is not None:
            parsed = parse_quota_summary(cache.get("payload"), group)
            age = now_ts - float(cache.get("fetched_at", 0) or 0)
            if parsed is not None and age < 3600:
                return {"available": True, "error": "antigravity not running; serving cache",
                        **_clamp_expired(parsed, now_ts)}
        return {**unavailable, "error": "antigravity language_server not running"}

    pid, token = found
    payload = fetch_quota_summary(pid, token)
    parsed = parse_quota_summary(payload, group)
    if parsed is not None:
        _save_cache(payload)
        return {"available": True, "error": "", **_clamp_expired(parsed, now_ts)}

    # RPC failed — fall back to any cache, however old, before giving up.
    if cache is not None:
        parsed = parse_quota_summary(cache.get("payload"), group)
        if parsed is not None:
            return {"available": True, "error": "rpc failed; serving cache",
                    **_clamp_expired(parsed, now_ts)}
    return {**unavailable, "error": "RetrieveUserQuotaSummary returned no bucket data"}
