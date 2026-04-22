#!/bin/bash

# Configuration
PROJECT_DIR="/Users/danielkuschmierz/Woolies Script"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

WORKER_PLIST="com.wooliesbot.automation.plist"
LEGACY_CHEFOS_PLIST="com.chefsos.plist"
LEGACY_CHEFOS_AUTOMATION="com.chefos.automation.plist"

function install() {
    echo "⚙️ Installing WooliesBot Automation Service..."
    
    # 1. Remove legacy LaunchAgents (duplicate chef_os or old names)
    if [ -f "$LAUNCH_AGENTS_DIR/$LEGACY_CHEFOS_PLIST" ]; then
        echo "   - Removing legacy $LEGACY_CHEFOS_PLIST (use $WORKER_PLIST for chef_os)..."
        launchctl unload "$LAUNCH_AGENTS_DIR/$LEGACY_CHEFOS_PLIST" 2>/dev/null
        rm -f "$LAUNCH_AGENTS_DIR/$LEGACY_CHEFOS_PLIST"
    fi
    if [ -f "$LAUNCH_AGENTS_DIR/$LEGACY_CHEFOS_AUTOMATION" ]; then
        echo "   - Removing legacy $LEGACY_CHEFOS_AUTOMATION..."
        launchctl unload "$LAUNCH_AGENTS_DIR/$LEGACY_CHEFOS_AUTOMATION" 2>/dev/null
        rm -f "$LAUNCH_AGENTS_DIR/$LEGACY_CHEFOS_AUTOMATION"
    fi

    # 2. Copy automation plist
    cp "$PROJECT_DIR/$WORKER_PLIST" "$LAUNCH_AGENTS_DIR/"
    
    # 3. Load service
    launchctl load "$LAUNCH_AGENTS_DIR/$WORKER_PLIST"
    
    echo "✅ Automation service installed and started!"
}

function stop() {
    echo "🛑 Stopping WooliesBot automation..."
    launchctl unload "$LAUNCH_AGENTS_DIR/$WORKER_PLIST" 2>/dev/null
    launchctl unload "$LAUNCH_AGENTS_DIR/$LEGACY_CHEFOS_PLIST" 2>/dev/null
    echo "✅ Services stopped."
}

function restart() {
    stop
    install
}

function status() {
    echo "📊 WooliesBot Service Status:"
    echo "--- Automation ---"
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
