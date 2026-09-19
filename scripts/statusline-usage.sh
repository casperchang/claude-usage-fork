#!/usr/bin/env bash
# Claude Code statusLine command.
# 1) Dumps the rate-limit payload Claude Code passes on stdin to a JSON file,
#    which claude-usage-widget reads (statusline_cache_path) instead of
#    polling the rate-limited /api/oauth/usage endpoint.
# 2) Prints a compact status line: model · 5h % · 7d %.
exec python3 -c '
import json, os, sys, tempfile
from datetime import datetime

try:
    data = json.load(sys.stdin)
except Exception:
    print("claude"); sys.exit(0)

model = (data.get("model") or {}).get("display_name") or "claude"
rl = data.get("rate_limits") or {}
five, seven = rl.get("five_hour") or {}, rl.get("seven_day") or {}

if five.get("used_percentage") is not None and seven.get("used_percentage") is not None:
    out = os.path.expanduser("~/.cache/claude-usage/statusline.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    payload = {"captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "rate_limits": {"five_hour": five, "seven_day": seven}}
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out))
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh)
    os.replace(tmp, out)  # atomic, so the widget never reads a half-written file
    print("%s · 5h %.0f%% · 7d %.0f%%" % (model, five["used_percentage"], seven["used_percentage"]))
else:
    print(model)
'
