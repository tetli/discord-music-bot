#!/usr/bin/env bash
# Start the music bot. Press Ctrl+C to stop it.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "First run: creating a virtual environment..."
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
fi

echo "Updating yt-dlp..."
.venv/bin/python -m pip install -q -U yt-dlp

echo "Starting the bot..."
exec .venv/bin/python -m musicbot.main
