"""Tests for the opt-in Gemini / Google AI Pro provider (claude_usage.gemini)."""

import json
import time
from unittest.mock import patch

import claude_usage.gemini as gemini
from claude_usage.gemini import _clamp_expired, _parse_reset, parse_quota_summary


FUTURE = int(time.time()) + 3600
PAST = int(time.time()) - 3600


def _iso(ts: int) -> str:
    """RFC3339 the way the Connect JSON mapping renders a Timestamp."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _payload(buckets, group="gemini"):
    return {"response": {"groups": [{"displayName": "Gemini Models",
                                     "buckets": buckets}]}}


def _bucket(bucket_id, window, remaining, reset_ts):
    b = {"bucketId": bucket_id, "window": window, "remainingFraction": remaining}
    if reset_ts is not None:
        b["resetTime"] = _iso(reset_ts)
    return b


# --------------------------------------------------------------- parsing

def test_parse_both_windows_converts_remaining_to_used():
    parsed = parse_quota_summary(_payload([
        _bucket("gemini-5h", "5h", 0.75, FUTURE),
        _bucket("gemini-weekly", "weekly", 0.4, FUTURE + 86400),
    ]))
    assert parsed is not None
    # remainingFraction is REMAINING; the widget draws USED.
    assert abs(parsed["session_pct"] - 0.25) < 1e-9
    assert abs(parsed["weekly_pct"] - 0.6) < 1e-9
    assert parsed["session_reset"] == FUTURE
    assert parsed["weekly_reset"] == FUTURE + 86400


def test_parse_selects_requested_group():
    payload = {"response": {"groups": [
        {"displayName": "Gemini Models", "buckets": [
            _bucket("gemini-weekly", "weekly", 0.9, FUTURE)]},
        {"displayName": "Claude and GPT models", "buckets": [
            _bucket("3p-weekly", "weekly", 0.5, FUTURE)]},
    ]}}
    assert abs(parse_quota_summary(payload, "gemini")["weekly_pct"] - 0.1) < 1e-9
    assert abs(parse_quota_summary(payload, "3p")["weekly_pct"] - 0.5) < 1e-9


def test_parse_single_window_and_clamping():
    parsed = parse_quota_summary(_payload([
        _bucket("gemini-5h", "5h", -2.0, None),  # nonsense → clamped
    ]))
    assert parsed is not None
    assert parsed["session_pct"] == 1.0  # clamped to 0..1
    assert parsed["session_reset"] == 0
    assert parsed["weekly_pct"] == 0.0


def test_parse_falls_back_to_bucket_id_suffix_without_window_field():
    parsed = parse_quota_summary(_payload([
        {"bucketId": "gemini-5h", "remainingFraction": 0.5,
         "resetTime": _iso(FUTURE)},
        {"bucketId": "gemini-weekly", "remainingFraction": 0.25,
         "resetTime": _iso(FUTURE)},
    ]))
    assert parsed is not None
    assert abs(parsed["session_pct"] - 0.5) < 1e-9
    assert abs(parsed["weekly_pct"] - 0.75) < 1e-9


def test_parse_accepts_bare_body_without_response_wrapper():
    parsed = parse_quota_summary(
        {"groups": [{"buckets": [_bucket("gemini-5h", "5h", 0.8, FUTURE)]}]})
    assert parsed is not None
    assert abs(parsed["session_pct"] - 0.2) < 1e-9


def test_parse_rejects_unusable_payloads():
    assert parse_quota_summary(None) is None
    assert parse_quota_summary({}) is None
    assert parse_quota_summary({"response": {"groups": []}}) is None
    assert parse_quota_summary({"code": "unauthenticated"}) is None
    # A bucket with no fraction carries no percentage to draw.
    assert parse_quota_summary(_payload([{"bucketId": "gemini-5h",
                                          "remainingAmount": 12}])) is None
    # Right shape, wrong group.
    assert parse_quota_summary(_payload([
        _bucket("gemini-5h", "5h", 0.5, FUTURE)]), "3p") is None


def test_parse_reset_handles_encodings():
    assert _parse_reset("2026-09-23T02:48:50Z") == 1790131730
    assert _parse_reset("2026-09-23T02:48:50.123456789Z") == 1790131730
    assert _parse_reset(1790131730) == 1790131730
    assert _parse_reset(1790131730 * 1000) == 1790131730  # millis normalised
    assert _parse_reset(None) == 0
    assert _parse_reset("") == 0
    assert _parse_reset("not a date") == 0


def test_parses_the_real_antigravity_payload():
    """Shape captured from a live Antigravity RetrieveUserQuotaSummary."""
    payload = json.loads("""
    {"response": {"groups": [
      {"displayName": "Gemini Models",
       "buckets": [
         {"bucketId": "gemini-weekly", "displayName": "Weekly Limit Remaining",
          "window": "weekly", "remainingFraction": 0.9679643,
          "resetTime": "2026-09-23T02:48:50Z"},
         {"bucketId": "gemini-5h", "displayName": "Five Hour Limit Remaining",
          "window": "5h", "remainingFraction": 1,
          "resetTime": "2026-09-20T13:57:55Z"}]},
      {"displayName": "Claude and GPT models",
       "buckets": [
         {"bucketId": "3p-weekly", "window": "weekly",
          "remainingFraction": 0.68646467,
          "resetTime": "2026-09-23T03:24:51Z"}]}]}}
    """)
    parsed = parse_quota_summary(payload)
    assert abs(parsed["weekly_pct"] - 0.0320357) < 1e-7
    assert parsed["session_pct"] == 0.0
    assert parsed["weekly_reset"] == 1790131730
    third_party = parse_quota_summary(payload, "3p")
    assert abs(third_party["weekly_pct"] - 0.31353533) < 1e-7


def test_clamp_expired_windows_roll_back_to_zero():
    now = time.time()
    parsed = {"session_pct": 0.5, "session_reset": int(now) - 10,
              "weekly_pct": 0.3, "weekly_reset": int(now) + 600}
    out = _clamp_expired(parsed, now)
    assert out["session_pct"] == 0.0 and out["session_reset"] == 0
    assert out["weekly_pct"] == 0.3  # still inside its window


# ------------------------------------------------------------- discovery

PS_LINE = ("  155840 /home/u/.local/share/antigravity/resources/bin/"
           "language_server --standalone --csrf_token abc123 --app_data_dir antigravity")


def test_find_language_server_reads_pid_and_token():
    with patch.object(gemini, "_run", return_value=PS_LINE):
        assert gemini.find_language_server() == (155840, "abc123")


def test_find_language_server_accepts_equals_form():
    line = "  42 /x/language_server --csrf_token=tok99 --standalone"
    with patch.object(gemini, "_run", return_value=line):
        assert gemini.find_language_server() == (42, "tok99")


def test_find_language_server_absent_when_not_running():
    with patch.object(gemini, "_run", return_value="  1 /usr/bin/other\n"):
        assert gemini.find_language_server() is None


def test_find_language_server_ignores_process_without_token():
    with patch.object(gemini, "_run", return_value="  7 /x/language_server --standalone"):
        assert gemini.find_language_server() is None


def test_find_listening_ports_parses_ss_output():
    ss_out = ('LISTEN 0 4096 127.0.0.1:34377 0.0.0.0:* '
              'users:(("language_server",pid=155840,fd=19))\n'
              'LISTEN 0 4096 127.0.0.1:37759 0.0.0.0:* '
              'users:(("language_server",pid=155840,fd=22))\n'
              'LISTEN 0 4096 127.0.0.1:9999 0.0.0.0:* '
              'users:(("other",pid=1,fd=3))\n')
    with patch.object(gemini, "_run", return_value=ss_out):
        assert gemini.find_listening_ports(155840) == [34377, 37759]


# -------------------------------------------------------------- collection

def test_collect_non_posix_is_unavailable():
    with patch.object(gemini.os, "name", "nt"):
        out = gemini.collect_gemini()
    assert out["available"] is False
    assert "POSIX" in out["error"]


def test_collect_without_antigravity_is_unavailable():
    with patch.object(gemini, "_load_cache", return_value=None), \
         patch.object(gemini, "find_language_server", return_value=None):
        out = gemini.collect_gemini()
    assert out["available"] is False
    assert "not running" in out["error"]


def test_collect_serves_fresh_cache_without_calling_rpc():
    cache = {"fetched_at": time.time(),
             "payload": _payload([_bucket("gemini-weekly", "weekly", 0.4, FUTURE)])}
    with patch.object(gemini, "_load_cache", return_value=cache), \
         patch.object(gemini, "find_language_server") as find:
        out = gemini.collect_gemini(poll_seconds=300)
    find.assert_not_called()  # fresh cache → no discovery, no RPC
    assert out["available"] is True
    assert abs(out["weekly_pct"] - 0.6) < 1e-9


def test_collect_rpc_success_saves_cache():
    payload = _payload([_bucket("gemini-5h", "5h", 0.2, FUTURE)])
    with patch.object(gemini, "_load_cache", return_value=None), \
         patch.object(gemini, "find_language_server", return_value=(1, "tok")), \
         patch.object(gemini, "fetch_quota_summary", return_value=payload), \
         patch.object(gemini, "_save_cache") as save:
        out = gemini.collect_gemini()
    save.assert_called_once_with(payload)
    assert out["available"] is True
    assert abs(out["session_pct"] - 0.8) < 1e-9


def test_collect_rpc_failure_falls_back_to_stale_cache():
    stale = {"fetched_at": time.time() - 99999,
             "payload": _payload([_bucket("gemini-weekly", "weekly", 0.1, FUTURE)])}
    with patch.object(gemini, "_load_cache", return_value=stale), \
         patch.object(gemini, "find_language_server", return_value=(1, "tok")), \
         patch.object(gemini, "fetch_quota_summary", return_value=None):
        out = gemini.collect_gemini()
    assert out["available"] is True
    assert "serving cache" in out["error"]
    assert abs(out["weekly_pct"] - 0.9) < 1e-9


def test_collect_serves_recent_cache_when_ide_closed():
    recent = {"fetched_at": time.time() - 120,
              "payload": _payload([_bucket("gemini-weekly", "weekly", 0.5, FUTURE)])}
    with patch.object(gemini, "_load_cache", return_value=recent), \
         patch.object(gemini, "find_language_server", return_value=None):
        # poll_seconds=0 forces past the fresh-cache shortcut into discovery.
        out = gemini.collect_gemini(poll_seconds=0)
    assert out["available"] is True
    assert "not running" in out["error"]


def test_collect_expired_window_reports_zero_not_stale():
    cache = {"fetched_at": time.time(),
             "payload": _payload([_bucket("gemini-5h", "5h", 0.1, PAST)])}
    with patch.object(gemini, "_load_cache", return_value=cache):
        out = gemini.collect_gemini(poll_seconds=300)
    assert out["available"] is True
    assert out["session_pct"] == 0.0  # window rolled over
    assert out["session_reset"] == 0
