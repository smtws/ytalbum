#!/usr/bin/env python3
"""
Simple WebSocket test client for the YouTube Music Downloader
"""

import asyncio
import websockets
import json

async def test_websocket():
    uri = "ws://127.0.0.1:7000/ws"
    
    try:
        async with websockets.connect(uri) as websocket:
            print(f"Connected to {uri}")
            
            # Test 1: Get initial state
            print("\n--- Test 1: Get initial state ---")
            await websocket.send(json.dumps({
                "type": "get_state",
                "data": {}
            }))
            
            response = await websocket.recv()
            data = json.loads(response)
            print(f"Initial state: {data['data']['status']}")
            print(f"Results count: {len(data['data']['results'])}")
            
            # Test 2: Add dummy data
            print("\n--- Test 2: Add dummy data ---")
            await websocket.send(json.dumps({
                "type": "test_dummy_data",
                "data": {}
            }))
            
            response = await websocket.recv()
            data = json.loads(response)
            print(f"After dummy data - Results count: {len(data['data']['results'])}")
            
            # Print first result
            if data['data']['results']:
                first_result = data['data']['results'][0]
                print(f"First result: {first_result['title']} by {first_result['artist']}")
                print(f"Verified: {first_result['verified']}")
            
            # Test 3: Start search
            print("\n--- Test 3: Start search ---")
            await websocket.send(json.dumps({
                "type": "start_search",
                "data": {"query": "Rick Astley"}
            }))
            
            response = await websocket.recv()
            data = json.loads(response)
            print(f"Search started - Status: {data['data']['status']}")
            print(f"Query: {data['data']['search_query']}")
            print(f"Strategies: {data['data']['search_strategies_total']}")
            
            # Test 4: Clear results
            print("\n--- Test 4: Clear results ---")
            await websocket.send(json.dumps({
                "type": "clear_results",
                "data": {}
            }))
            
            response = await websocket.recv()
            data = json.loads(response)
            print(f"After clear - Status: {data['data']['status']}")
            print(f"Results count: {len(data['data']['results'])}")
            
    except Exception as e:
        print(f"WebSocket test failed: {e}")

if __name__ == "__main__":
    print("Testing WebSocket connection to YouTube Music Downloader...")
    asyncio.run(test_websocket())