#!/bin/bash
# ==============================================================================
# GateStream Auto-Restart Watchdog
# Automatically checks if python -m bot is running and revives it if stopped.
# Add to crontab: */5 * * * * /path/to/GateStream/scripts/watchdog.sh
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

# Check if the bot process is currently active
if ! pgrep -f "python -m bot" > /dev/null 2>&1; then
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$TIMESTAMP] GateStream bot process not found. Reviving..." >> "$PROJECT_DIR/watchdog.log"

    # Activate virtual environment if available
    if [ -f "$PROJECT_DIR/venv/bin/activate" ]; then
        source "$PROJECT_DIR/venv/bin/activate"
    fi

    # Start bot in background with nohup
    nohup python -m bot >> "$PROJECT_DIR/bot.log" 2>&1 &
    NEW_PID=$!
    echo "[$TIMESTAMP] GateStream successfully restarted with PID $NEW_PID." >> "$PROJECT_DIR/watchdog.log"
fi