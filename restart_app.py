#!/usr/bin/env python3
"""
Graceful restart script for YouTube Music Downloader
Kills existing processes and starts fresh instances
"""

import os
import signal
import subprocess
import time
import sys

def kill_processes_on_port(port):
    """Kill processes listening on specified port"""
    try:
        # Get PIDs listening on port
        result = subprocess.run(['lsof', '-ti', f':{port}'], 
                              capture_output=True, text=True)
        if result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                try:
                    print(f"Killing process {pid} on port {port}")
                    os.kill(int(pid), signal.SIGTERM)
                    time.sleep(0.5)  # Give it time to terminate gracefully
                    # Force kill if still running
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # Already dead
                except (ValueError, ProcessLookupError):
                    pass
        print(f"Cleared port {port}")
    except subprocess.CalledProcessError:
        print(f"No processes found on port {port}")

def main():
    print("🔄 Gracefully restarting YouTube Music Downloader...")
    
    # Kill processes on both ports
    print("🛑 Stopping existing processes...")
    kill_processes_on_port(7002)  # Backend
    kill_processes_on_port(5173)  # Frontend
    
    # Wait a moment for cleanup
    time.sleep(1)
    
    print("🚀 Starting fresh processes...")
    
    # Start backend in background
    print("Starting backend on port 7002...")
    backend_cmd = [
        'python3', '-m', 'uvicorn', 'backend.app:app',
        '--host', '127.0.0.1', '--port', '7002', '--log-level', 'info'
    ]
    
    env = os.environ.copy()
    env['PYTHONPATH'] = '/home/tordt/YT-Downloads'
    
    backend_proc = subprocess.Popen(backend_cmd, env=env, cwd='/home/tordt/YT-Downloads')
    
    # Wait a moment for backend to start
    time.sleep(2)
    
    # Start frontend in background
    print("Starting frontend on port 5173...")
    frontend_cmd = ['npm', 'run', 'dev']
    frontend_proc = subprocess.Popen(frontend_cmd, cwd='/home/tordt/YT-Downloads/frontend')
    
    print(f"✅ Backend started with PID {backend_proc.pid}")
    print(f"✅ Frontend started with PID {frontend_proc.pid}")
    print("🌐 Application should be available at:")
    print("   - Frontend: http://localhost:5173")
    print("   - Backend:  http://localhost:7002")
    print("\n💡 Counter synchronization fix is now active!")

if __name__ == "__main__":
    main()