#!/usr/bin/env python3
"""
Test script to verify normalized and metadata fields appear in results
"""
import asyncio
import json
import websockets
import time

async def test_metadata_fields():
    """Test that normalized and metadata fields appear in real-time"""
    print("Testing normalized and metadata fields...")
    
    uri = "ws://localhost:7003/ws"
    
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to WebSocket")
            
            # Start a search
            search_command = {
                "type": "start_search",
                "data": {"query": "Sabaton The Last Stand"}
            }
            await websocket.send(json.dumps(search_command))
            print("Sent search command")
            
            # Monitor for results with normalized/metadata fields
            found_normalized = False
            found_metadata = False
            result_count = 0
            
            timeout = time.time() + 30  # 30 second timeout
            
            while time.time() < timeout:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=2)
                    data = json.loads(message)
                    
                    if data.get("type") == "state_update" and data.get("data"):
                        app_state = data["data"]
                        results = app_state.get("results", [])
                        
                        if len(results) > result_count:
                            result_count = len(results)
                            print(f"Found {result_count} results so far...")
                            
                            # Check the latest results for our fields
                            for result in results[-3:]:  # Check last 3 results
                                result_id = result.get("id", "unknown")
                                title = result.get("title", "unknown")
                                
                                # Check for normalized field
                                normalized = result.get("normalized", {})
                                if normalized:
                                    print(f"✅ Result {result_id[:8]} '{title[:30]}...' has normalized: {normalized}")
                                    found_normalized = True
                                
                                # Check for metadata field
                                metadata = result.get("metadata", {})
                                if metadata:
                                    print(f"✅ Result {result_id[:8]} '{title[:30]}...' has metadata: {list(metadata.keys())}")
                                    found_metadata = True
                        
                        # Check if we found both
                        if found_normalized and found_metadata:
                            print("\n🎉 SUCCESS: Both normalized and metadata fields found in real-time!")
                            return True
                            
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    print(f"Error processing message: {e}")
                    continue
            
            print(f"\n❌ TIMEOUT: After 30 seconds, found {result_count} results")
            print(f"   - Normalized fields found: {found_normalized}")
            print(f"   - Metadata fields found: {found_metadata}")
            
            if result_count > 0:
                print("\nLast result sample:")
                if results:
                    last_result = results[-1]
                    print(f"   Title: {last_result.get('title', 'N/A')}")
                    print(f"   Normalized: {last_result.get('normalized', 'N/A')}")
                    print(f"   Metadata: {last_result.get('metadata', 'N/A')}")
            
            return False
            
    except Exception as e:
        print(f"Connection error: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_metadata_fields())
    exit(0 if success else 1)