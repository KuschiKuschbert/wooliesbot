# Auto-start WoolesBot on boot (macOS)

1. Edit `com.chefsos.plist` and set `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` in the `EnvironmentVariables` section.

2. If using a venv elsewhere, update the `ProgramArguments` path to your `python` executable.

3. Copy the plist:
   ```bash
   cp com.chefsos.plist ~/Library/LaunchAgents/
   ```

4. Load and start:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.chefsos.plist
   ```

5. To stop:
   ```bash
   launchctl unload ~/Library/LaunchAgents/com.chefsos.plist
   ```
