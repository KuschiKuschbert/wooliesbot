# Auto-start WoolesBot on boot (macOS)

Services are defined in the repo as `com.wooliesbot.api.plist` and `com.wooliesbot.automation.plist` (the latter runs `chef_os.py`). Both use the project `.venv` Python.

**Install or restart (recommended):**

```bash
cd "/Users/danielkuschmierz/Woolies Script"
chmod +x manage_services.sh
./manage_services.sh install
```

**Stop** (so processes stay down — required before manual `chef_os.py` tests if you do not want launchd to relaunch):

```bash
./manage_services.sh stop
```

**Why `kill` seemed to “respawn” the bot:** `KeepAlive` in the plist tells launchd to restart `chef_os.py` when it exits. Use `manage_services.sh stop` or `launchctl unload ~/Library/LaunchAgents/com.wooliesbot.automation.plist` instead of killing the PID.

Telegram credentials belong in `.env` (loaded by `chef_os.py`); do not embed tokens in plist files.

Optional **scraper / anti-bot tuning** (`WOOLIESBOT_*`) is documented in `.env.example`; copy keys into `.env` only if you need to override defaults.
