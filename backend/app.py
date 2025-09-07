#!/usr/bin/env python3
"""
YouTube Music Downloader - FastAPI Application
WebSocket-based real-time communication with Vue.js frontend
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import json
import os

from backend.core.state_manager import StateManager
from backend.core.models import Result

# Initialize FastAPI app
app = FastAPI(title="YouTube Music Downloader")

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
            # Default search strategies
            strategies = [
                "channel_search",
                "youtube_music_search", 
                "playlist_search",
                "direct_album_search",
                "google_search"
            ]
            await state_manager.start_search(query, strategies)
        else:
            print("Empty search query received")
    
    elif msg_type == "clear_results":
        await state_manager.clear_state()
    
    elif msg_type == "get_state":
        await state_manager.send_state()
    
    elif msg_type == "test_dummy_data":
        # Test with dummy data
        await test_with_dummy_data()
    
    elif msg_type == "refresh_cookies":
        await state_manager.refresh_cookie_detection()
    
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

if __name__ == "__main__":
    import uvicorn
    print("Starting YouTube Music Downloader API...")
    print("WebSocket endpoint: ws://localhost:8000/ws")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")