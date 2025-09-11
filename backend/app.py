#!/usr/bin/env python3
"""
YouTube Music Downloader - FastAPI Application
WebSocket-based real-time communication with Vue.js frontend

WARNING: DO NOT run this directly with 'python3 -m uvicorn backend.app:app'!
Use './start-backend.sh' instead for proper process management.
"""

import os
import sys

# Hint about proper usage if run directly
if __name__ == "__main__" or "uvicorn" in sys.argv[0]:
    # Check if we're being run through the proper script
    if not os.path.exists("/tmp/ytdl-backend.pid"):
        print("⚠️  WARNING: Backend should be started via './start-backend.sh'")
        print("   This ensures proper process management and prevents multiple instances.")
        print("   Use './start-backend.sh start' instead of running uvicorn directly!")
        print()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import socket
import ipaddress
import logging

from backend.core.state_manager import StateManager
from backend.core.models import Result

logger = logging.getLogger(__name__)

def get_network_origins():
    """Automatically detect network ranges and generate allowed origins"""
    origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    
    try:
        # Get all network interfaces
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        
        # Add the detected local IP
        origins.append(f"http://{local_ip}:3000")
        
        # Try to detect common private network ranges
        ip = ipaddress.IPv4Address(local_ip)
        
        # Generate origins for the detected network range
        if ip.is_private:
            network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
            
            # Add a few common IPs from the network range for development
            for i in [1, 2, 10, 50, 100, 133, 150, 200]:
                try:
                    test_ip = str(network.network_address + i)
                    if test_ip != str(network.network_address) and test_ip != str(network.broadcast_address):
                        origins.append(f"http://{test_ip}:3000")
                except:
                    continue
        
        logger.info(f"Detected local IP: {local_ip}")
        logger.info(f"Generated CORS origins: {origins[:10]}...")  # Show first 10
        
    except Exception as e:
        logger.warning(f"Could not detect network configuration: {e}")
        logger.info("Falling back to wildcard CORS for development")
        return ["*"]
    
    return origins

# Initialize FastAPI app
app = FastAPI(title="YouTube Music Downloader")

# Add CORS middleware with network detection
allowed_origins = get_network_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state manager instance
state_manager = StateManager()

# Serve static files (Vue.js frontend)
if os.path.exists("frontend/dist"):
    app.mount("/static", StaticFiles(directory="frontend/dist"), name="static")

@app.get("/")
async def read_root():
    """Serve Vue.js frontend"""
    if os.path.exists("frontend/dist/index.html"):
        return FileResponse("frontend/dist/index.html")
    return {"message": "YouTube Music Downloader API", "status": "running"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Main WebSocket endpoint for real-time communication"""
    await websocket.accept()
    await state_manager.add_websocket(websocket)
    
    print(f"WebSocket connected. Total connections: {len(state_manager.websockets)}")
    
    try:
        while True:
            # Receive message from frontend
            data = await websocket.receive_text()
            message = json.loads(data)
            
            await handle_websocket_message(message)
            
    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await state_manager.remove_websocket(websocket)

async def handle_websocket_message(message: dict):
    """Handle incoming WebSocket messages from frontend"""
    msg_type = message.get("type")
    data = message.get("data", {})
    
    print(f"Received message: {msg_type}")
    
    if msg_type == "start_search":
        query = data.get("query", "").strip()
        if query:
            # Default search strategies - use all available services
            await state_manager.start_search(query)
        else:
            print("Empty search query received")
    
    elif msg_type == "clear_results":
        await state_manager.clear_state()
    
    elif msg_type == "get_state":
        await state_manager.send_state()
    
    elif msg_type == "test_dummy_data":
        # Test with dummy data
        await test_with_dummy_data()
    
    else:
        print(f"Unknown message type: {msg_type}")

async def test_with_dummy_data():
    """Test the system with dummy search results"""
    print("Adding dummy test data...")
    
    # Create dummy results
    dummy_results = [
        Result(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            youtube_id="dQw4w9WgXcQ",
            title="Rick Astley - Never Gonna Give You Up",
            artist="Rick Astley",
            channel="RickAstleyVEVO",
            view_count=1000000,
            discovered_by="test_service"
        ),
        Result(
            youtube_url="https://www.youtube.com/watch?v=oHg5SJYRHA0", 
            youtube_id="oHg5SJYRHA0",
            title="RickRoll'D",
            artist="Rick Astley",
            channel="OfficialRickAstley",
            view_count=500000,
            discovered_by="test_service"
        ),
        Result(
            youtube_url="https://www.youtube.com/watch?v=L_jWHffIx5E",
            youtube_id="L_jWHffIx5E", 
            title="Smash Mouth - All Star",
            artist="Smash Mouth",
            channel="SmashMouthVEVO",
            view_count=750000,
            discovered_by="test_service"
        )
    ]
    
    # Add results through state manager
    for result in dummy_results:
        await state_manager.add_result(result)
    
    # Test verification update
    if state_manager.state.results:
        first_result = state_manager.state.results[0]
        await state_manager.update_result(
            first_result.id, 
            verified=True,
            verification_metadata={"musicbrainz_id": "test-mb-id", "confidence": 0.95}
        )
    
    print(f"Added {len(dummy_results)} dummy results")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    cookie_info = state_manager.state.config.cookie_info
    return {
        "status": "healthy",
        "connected_clients": len(state_manager.websockets),
        "current_state": state_manager.state.status,
        "results_count": state_manager.get_result_count(),
        "cookie_support": {
            "browsers_detected": cookie_info.browsers_detected,
            "recommended_browser": cookie_info.recommended_browser,
            "cookie_support_available": cookie_info.cookie_support_available,
            "yt_dlp_compatible": cookie_info.yt_dlp_compatible
        }
    }

@app.post("/clear")
async def clear_state():
    """Clear all search results and reset state"""
    await state_manager.clear_state()
    return {"status": "cleared", "message": "All search results and state cleared"}

if __name__ == "__main__":
    import uvicorn
    import logging
    from backend.services.instance_manager import cleanup_existing_instances
    
    # Setup logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    
    try:
        # Clean up any existing instances and get a free port
        logger.info("YouTube Music Downloader starting up...")
        port = cleanup_existing_instances()
        
        print(f"Starting YouTube Music Downloader API on port {port}...")
        print(f"WebSocket endpoint: ws://localhost:{port}/ws")
        print(f"Health endpoint: http://localhost:{port}/health")
        
        # Start the server
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
        
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        raise