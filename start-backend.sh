#!/bin/bash
# YouTube Music Downloader - Backend Process Manager
# This script ensures only one backend instance runs at a time
# Usage: ./start-backend.sh
# DO NOT run 'python3 -m uvicorn backend.app:app' directly - use this script instead!

set -e

PROCESS_NAME="ytdl-backend"
PID_FILE="/tmp/${PROCESS_NAME}.pid"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Function to check if process is running
is_process_running() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0  # Process is running
        else
            rm -f "$PID_FILE"  # Remove stale PID file
            return 1  # Process not running
        fi
    fi
    return 1  # PID file doesn't exist
}

# Function to stop existing process
stop_backend() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        echo "Stopping existing $PROCESS_NAME (PID: $pid)..."
        if kill -TERM "$pid" 2>/dev/null; then
            # Wait for graceful shutdown
            local count=0
            while kill -0 "$pid" 2>/dev/null && [ $count -lt 10 ]; do
                sleep 1
                count=$((count + 1))
            done
            
            # Force kill if still running
            if kill -0 "$pid" 2>/dev/null; then
                echo "Force killing $PROCESS_NAME..."
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$PID_FILE"
        echo "✓ $PROCESS_NAME stopped"
    fi
}

# Function to start backend
start_backend() {
    echo "Starting $PROCESS_NAME..."
    cd "$SCRIPT_DIR"
    
    # Set environment and start process in background
    export PYTHONPATH="$SCRIPT_DIR"
    
    # Start backend directly in background and capture PID
    nohup python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 7002 --log-level info > /tmp/ytdl-backend.log 2>&1 &
    local backend_pid=$!
    
    # Write PID file
    echo "$backend_pid" > "$PID_FILE"
    
    # Wait a moment for startup
    sleep 3
    
    if is_process_running; then
        echo "✓ $PROCESS_NAME started successfully (PID: $backend_pid)"
        echo "Backend running on http://127.0.0.1:7002"
        echo "Logs: /tmp/ytdl-backend.log"
    else
        echo "✗ Failed to start $PROCESS_NAME"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# Handle command line arguments
case "${1:-start}" in
    start)
        if is_process_running; then
            pid=$(cat "$PID_FILE")
            echo "$PROCESS_NAME is already running (PID: $pid)"
            echo "Use './start-backend.sh restart' to restart"
            exit 1
        fi
        start_backend
        ;;
    stop)
        if is_process_running; then
            stop_backend
        else
            echo "$PROCESS_NAME is not running"
        fi
        ;;
    restart)
        echo "Restarting $PROCESS_NAME..."
        stop_backend
        sleep 1
        start_backend
        ;;
    status)
        if is_process_running; then
            pid=$(cat "$PID_FILE")
            echo "$PROCESS_NAME is running (PID: $pid)"
        else
            echo "$PROCESS_NAME is not running"
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        echo ""
        echo "  start   - Start the backend process"
        echo "  stop    - Stop the backend process"
        echo "  restart - Restart the backend process"
        echo "  status  - Check if backend is running"
        echo ""
        echo "DO NOT run 'python3 -m uvicorn backend.app:app' directly!"
        echo "Always use this script to ensure proper process management."
        exit 1
        ;;
esac