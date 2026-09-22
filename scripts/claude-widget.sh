#!/usr/bin/env bash
# Control the Claude usage desktop widget (claude-usage-widget).
# Usage: ~/claude-widget.sh {start|stop|restart|status}

# "[b]in" keeps pgrep from matching this script's own command line.
PATTERN='[b]in/python[0-9.]* -m claude_usage'
LOG="$HOME/.cache/claude-usage/widget.log"
export PATH="$HOME/.local/bin:$PATH"

pids() { pgrep -f "$PATTERN"; }

start() {
    if pids >/dev/null; then
        echo "✅ Widget already running (PID $(pids | tr '\n' ' '))"
        return 0
    fi
    echo "Starting widget..."
    claude-usage --detach >/dev/null 2>&1
    for _ in $(seq 1 10); do
        pids >/dev/null && { echo "✅ Widget started (PID $(pids | tr '\n' ' '))"; return 0; }
        sleep 0.5
    done
    echo "❌ Widget did not start. Last log lines:"
    tail -n 10 "$LOG" 2>/dev/null
    return 1
}

stop() {
    if ! pids >/dev/null; then
        echo "✅ Widget is not running"
        return 0
    fi
    echo "Stopping widget..."
    pids | xargs -r kill
    for _ in $(seq 1 10); do
        pids >/dev/null || { echo "✅ Widget stopped"; return 0; }
        sleep 0.5
    done
    pids | xargs -r kill -9
    pids >/dev/null && { echo "❌ Could not stop widget"; return 1; }
    echo "✅ Widget stopped (forced)"
}

status() {
    if pids >/dev/null; then
        echo "✅ Widget running (PID $(pids | tr '\n' ' '))"
    else
        echo "⭕ Widget not running"
    fi
}

case "$1" in
    start)   start ;;
    stop)    stop ;;
    restart) stop && start ;;
    status)  status ;;
    *) echo "Usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
