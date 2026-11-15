# Discord Music Bot

A personal Discord music bot that plays YouTube audio in a voice channel. It runs on your own computer: start it when you want music, close it when you're done. It also plays `.txt` playlist files where each line is a YouTube link.

## Setup

### 1. Install Python and ffmpeg

- **Python 3.11 or newer**: <https://www.python.org/downloads/>. On Windows, tick "Add python.exe to PATH" in the installer.
- **ffmpeg** must be runnable as `ffmpeg` from a terminal:
  - Windows: `winget install Gyan.FFmpeg` (then open a new terminal), or download from <https://www.gyan.dev/ffmpeg/builds/> and add its `bin` folder to PATH.
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `sudo apt install ffmpeg`

Check with `ffmpeg -version`. The bot refuses to start if ffmpeg is missing.

### 2. Create the bot in the Discord Developer Portal

1. Go to <https://discord.com/developers/applications> and click **New Application**.
2. Open the **Bot** tab and click **Reset Token**. Copy the token (you'll paste it into `.env`). Treat it like a password.
   No privileged intents are needed; leave them off.
3. Open **OAuth2 → URL Generator**:
   - Scopes: `bot` and `applications.commands`
   - Bot permissions: `Connect` and `Speak` (plus `Send Messages` so it can announce tracks)
4. Open the generated URL and invite the bot to your server.

### 3. Configure

Copy `.env.example` to `.env` and paste your token:

```
DISCORD_TOKEN=your-token-here
```

`.env` is gitignored. Never share it.

### 4. Run

- **Windows**: double-click `start-bot.bat`.
- **macOS/Linux**: run `./start-bot.sh`.

The first run creates a `.venv` folder and installs the dependencies. Every run updates yt-dlp first, which fixes most "it suddenly stopped working" problems. When you see `Ready.` in the console, the bot is online. Close the window (or press Ctrl+C) to stop it.

You can also run it by hand: `pip install -r requirements.txt`, then `python -m musicbot.main`.

Slash commands may take a minute to show up in Discord the first time. If they don't, restart your Discord client (Ctrl+R).

## Commands

| Command | What it does |
|---|---|
| `/play <query>` | Play a YouTube link or search text (top result). A YouTube playlist link queues every video. |
| `/playlist load <name> [shuffle]` | Queue `playlists/<name>.txt`, optionally shuffled. |
| `/playlist list` | List playlist files and how many entries each has. |
| `/playlist add <name> <url>` | Append a link to a playlist file (creates it if needed). |
| `/pause`, `/resume` | Pause or resume. |
| `/skip` | Skip the current track. |
| `/stop` | Stop and clear the queue (stays in the channel). |
| `/leave` | Stop, clear, and leave the channel. |
| `/join` | Join your voice channel. |
| `/queue [page]` | Show the queue, 10 per page. |
| `/nowplaying` | Show the current track. |
| `/remove <position>` | Remove a track from the queue. |
| `/clear` | Clear the queue but keep the current track. |
| `/shuffle` | Shuffle the queue. |
| `/loop <off\|track\|queue>` | `track` repeats the current track, `queue` repeats the whole queue. |
| `/volume <0-100>` | Set the volume (default 50). |
| `/help` | List the commands. |

Control commands (skip, pause, volume, ...) only work if you're in the same voice channel as the bot. The bot leaves on its own after 5 minutes with nothing playing, or 2 minutes alone in the channel.

## Playlist files

Put `.txt` files in the `playlists/` folder. The file name (without `.txt`) is the playlist name, and may only contain letters, numbers, `-` and `_`.

```
# Chill mix
https://www.youtube.com/watch?v=dQw4w9WgXcQ
https://youtu.be/abcdefghijk

https://www.youtube.com/playlist?list=PLxxxxxxxx
```

- One YouTube link per line. Blank lines and lines starting with `#` are ignored.
- A YouTube playlist link is expanded into all of its videos.
- Lines that aren't YouTube links are skipped; the bot tells you which line numbers.
- Up to 500 tracks are queued per load.
- Loading is instant: titles and streams are looked up when each track starts playing. Videos that turn out to be removed or blocked are skipped automatically.

See `playlists/example.txt`.

## Troubleshooting

**"Sign in to confirm you're not a bot", HTTP 403, or tracks suddenly all fail**
1. Restart the bot with the start script (it updates yt-dlp). YouTube changes often and yt-dlp releases fixes quickly.
2. If it still fails, give yt-dlp your YouTube cookies: install a browser extension that exports cookies in Netscape `cookies.txt` format (for example "Get cookies.txt LOCALLY"), export them while signed in to YouTube, save the file as `cookies.txt` in the bot folder, and add to `.env`:
   ```
   COOKIES_FILE=cookies.txt
   ```
   `cookies.txt` is gitignored. Consider using a throwaway Google account.

**"ffmpeg was not found on PATH"**: install ffmpeg (see above) and open a new terminal/window so PATH is refreshed.

**"I need the Connect/Speak permission"**: give the bot's role those permissions on that voice channel.

**Slash commands don't appear**: make sure the invite URL included the `applications.commands` scope, then restart Discord.

**No sound, but "Now playing" shows and the bot has the green speaking ring**

Known cause: `davey` 0.2.0 (the library for Discord's end-to-end voice encryption, DAVE) is installed with discord.py 2.7.1. Since 0.2.0, davey only switches on its encryption key when the caller runs an extra "transition" step, and discord.py 2.7.1 doesn't do that. The bot joins the encrypted call, but never gets a key, so it sends unencrypted audio that everyone's Discord client silently drops. `requirements.txt` pins `davey<0.2` for this reason, the start scripts apply it on every run, and the bot prints a warning at startup if an incompatible davey is installed. Fix by hand: `pip install "davey<0.2"`. Only remove the pin once a discord.py release supports davey 0.2.

If that's not it:

1. Check the bot's user volume in Discord (right-click the bot in the voice channel) and `/volume`.
2. Make sure the voice extras are installed: `pip list | grep -i -E "nacl|davey"` should show both `PyNaCl` and `davey` (they come from `discord.py[voice]` in `requirements.txt`). `pip show discord.py` doesn't list them because they are optional extras.
3. Set `DEBUG_VOICE=1` in `.env`, restart, and play a song. Five seconds in, the console shows a `Voice health` line:
   - `frames_sent` stays near 0: audio isn't leaving ffmpeg/the player. Look for ffmpeg warnings just above it.
   - `frames_sent` grows (about 50 per second) but `dave_version` is above 0 and `dave_ready=False`: Discord's end-to-end voice encryption (DAVE) never finished setting up, so the bot sends audio listeners can't decrypt. Run `pip install -U discord.py davey` and reconnect the bot.
   - `frames_sent` grows and `dave_ready=True` (or `dave_version=0`): the audio is sent correctly but doesn't arrive. That points at the network path for voice (outgoing UDP): a VPN, firewall, or WSL2's NAT network mode. Try running the bot directly on Windows/macOS/Linux instead of in WSL, or turn off the VPN.

## Development

```
pip install -r requirements-dev.txt
python -m pytest
```

The tests never touch Discord or the network (yt-dlp and the voice client are faked).

Layout: `musicbot/main.py` (entry point), `config.py` (settings), `ytdl.py` (yt-dlp wrapper), `player.py` (queue and playback), `playlists.py` (playlist files), `commands.py` (slash commands).
