#!/bin/bash

# Configuration
PROJECT_DIR="/Users/danielkuschmierz/Woolies Script"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

API_PLIST="com.wooliesbot.api.plist"
WORKER_PLIST="com.wooliesbot.automation.plist"
OLD_PLIST="com.chefos.automation.plist"

function install() {
    echo "⚙️ Installing WooliesBot Services..."
    
    # 1. Unload old service if exists
    if [ -f "$LAUNCH_AGENTS_DIR/$OLD_PLIST" ]; then
        echo "   - Removing legacy service..."
        launchctl unload "$LAUNCH_AGENTS_DIR/$OLD_PLIST" 2>/dev/null
        rm "$LAUNCH_AGENTS_DIR/$OLD_PLIST"
    fi
    
    # 2. Copy new plists
    cp "$PROJECT_DIR/$API_PLIST" "$LAUNCH_AGENTS_DIR/"
    cp "$PROJECT_DIR/$WORKER_PLIST" "$LAUNCH_AGENTS_DIR/"
    
    # 3. Load services
    launchctl load "$LAUNCH_AGENTS_DIR/$API_PLIST"
    launchctl load "$LAUNCH_AGENTS_DIR/$WORKER_PLIST"
    
    echo "✅ Services installed and started!"
}

function stop() {
    echo "🛑 Stopping WooliesBot Services..."
    launchctl unload "$LAUNCH_AGENTS_DIR/$API_PLIST" 2>/dev/null
    launchctl unload "$LAUNCH_AGENTS_DIR/$WORKER_PLIST" 2>/dev/null
    echo "✅ Services stopped."
}

function restart() {
    stop
    install
}

function status() {
    echo "📊 WooliesBot Service Status:"
    echo "--- API ---"
    launchctl list | grep wooliesbot.api || echo "   Offline"
    echo "--- Price Tracker ---"
    launchctl list | grep wooliesbot.automation || echo "   Offline"
}

case "$1" in
    install)
        install
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {install|stop|restart|status}"
        exit 1
esac
