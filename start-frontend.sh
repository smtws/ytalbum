#!/bin/bash
# YouTube Music Downloader - Frontend Process Manager
# This script ensures only one frontend instance runs at a time
# Usage: ./start-frontend.sh
# DO NOT run 'npm run dev' directly - use this script instead!

set -e

PROCESS_NAME="ytdl-frontend"
PID_FILE="/tmp/${PROCESS_NAME}.pid"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

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
stop_frontend() {
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

# Function to start frontend
start_frontend() {
    echo "Starting $PROCESS_NAME..."
    
    # Check if frontend directory exists
    if [ ! -d "$FRONTEND_DIR" ]; then
        echo "✗ Frontend directory not found: $FRONTEND_DIR"
        echo "Make sure you're running this from the project root directory"
        exit 1
    fi
    
    # Check if package.json exists
    if [ ! -f "$FRONTEND_DIR/package.json" ]; then
        echo "✗ package.json not found in frontend directory"
        echo "Run 'npm install' in the frontend directory first"
        exit 1
    fi
    
    cd "$FRONTEND_DIR"
    
    # Start with process management
    (
        # Write PID file
        echo $$ > "$PID_FILE"
        
        # Cleanup PID file on exit
        trap 'rm -f "$PID_FILE"' EXIT
        
        # Hint about proper usage
        echo "$PROCESS_NAME started - use start-frontend.sh to manage this process"
        echo "PID file: $PID_FILE"
        
        # Run the actual frontend on specific port
        exec npm run dev -- --port 5174
    ) &
    
    # Wait a moment for startup
    sleep 3
    
    if is_process_running; then
        local pid=$(cat "$PID_FILE")
        echo "✓ $PROCESS_NAME started successfully (PID: $pid)"
        echo "Frontend running on http://localhost:5174"
    else
        echo "✗ Failed to start $PROCESS_NAME"
        exit 1
    fi
}

# Handle command line arguments
case "${1:-start}" in
    start)
        if is_process_running; then
            local pid=$(cat "$PID_FILE")
            echo "$PROCESS_NAME is already running (PID: $pid)"
            echo "Use './start-frontend.sh restart' to restart"
            exit 1
        fi
        start_frontend
        ;;
    stop)
        if is_process_running; then
            stop_frontend
        else
            echo "$PROCESS_NAME is not running"
        fi
        ;;
    restart)
        echo "Restarting $PROCESS_NAME..."
        stop_frontend
        sleep 1
        start_frontend
        ;;
    status)
        if is_process_running; then
            local pid=$(cat "$PID_FILE")
            echo "$PROCESS_NAME is running (PID: $pid)"
        else
            echo "$PROCESS_NAME is not running"
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        echo ""
        echo "  start   - Start the frontend process"
        echo "  stop    - Stop the frontend process"
        echo "  restart - Restart the frontend process"
        echo "  status  - Check if frontend is running"
        echo ""
        echo "DO NOT run 'npm run dev' directly in the frontend directory!"
        echo "Always use this script to ensure proper process management."
        exit 1
        ;;
esac