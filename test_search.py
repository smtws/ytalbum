#!/usr/bin/env python3
"""
Test the end-to-end search flow with mock data
"""

import sys
sys.path.append('/home/tordt/YT-Downloads')

import asyncio
from backend.core.state_manager import StateManager
from backend.services.youtube_deduplicator import YouTubeDeduplicator
from backend.core.models import Result

async def test_search_flow():
    """Test the complete search flow"""
    print("Testing YouTube Music Downloader Search Flow...")
    
    # Create state manager
    state_manager = StateManager()
    
    print("\n=== Test 1: Initial State ===")
    initial_state = await state_manager.get_state()
    print(f"Status: {initial_state.status}")
    print(f"Results count: {len(initial_state.results)}")
    
    print("\n=== Test 2: YouTube ID Deduplicator ===")
    # Test deduplicator with mock data
    result1 = YouTubeDeduplicator.prepare_result(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Rick Astley - Never Gonna Give You Up",
        artist="Rick Astley",
        channel="RickAstleyVEVO",
        discovered_by="test"
    )
    
    result2 = YouTubeDeduplicator.prepare_result(
        url="https://youtu.be/dQw4w9WgXcQ",  # Same video, different URL format
        title="Rick Astley - Never Gonna Give You Up (Official Music Video)",
        artist="Rick Astley", 
        channel="RickAstleyVEVO",
        discovered_by="test"
    )
    
    if result1:
        print(f"Result 1 ID: {result1.youtube_id}")
        added1 = await state_manager.add_result(result1)
        print(f"Result 1 added: {added1}")
    
    if result2:
        print(f"Result 2 ID: {result2.youtube_id}")  
        added2 = await state_manager.add_result(result2)
        print(f"Result 2 added (should be False - duplicate): {added2}")
    
    current_state = await state_manager.get_state()
    print(f"Results after deduplication: {len(current_state.results)}")
    print(f"Duplicates removed: {current_state.duplicates_removed}")
    
    print("\n=== Test 3: Search Service Integration ===")
    # Note: This will try to run real yt-dlp commands, so may fail without proper setup
    print("Starting search for 'Test Artist' (this may take a while or fail without proper yt-dlp setup)...")
    
    try:
        # This should work even if yt-dlp fails - the flow should be robust
        await state_manager.start_search("Test Artist", ["channel_search"])
        
        # Give it a few seconds to process
        await asyncio.sleep(5)
        
        final_state = await state_manager.get_state()
        print(f"Final status: {final_state.status}")
        print(f"Search query: {final_state.search_query}")
        print(f"Strategies completed: {len(final_state.search_strategies_completed)}/{len(final_state.search_strategies_total)}")
        print(f"Final results count: {len(final_state.results)}")
        
        if final_state.results:
            first_result = final_state.results[0]
            print(f"First result: {first_result.title} by {first_result.artist}")
            print(f"YouTube ID: {first_result.youtube_id}")
            print(f"Discovered by: {first_result.discovered_by}")
        
    except Exception as e:
        print(f"Search test failed (expected if yt-dlp not properly configured): {e}")
    
    print("\n=== Test Complete ===")
    return True

if __name__ == "__main__":
    asyncio.run(test_search_flow())