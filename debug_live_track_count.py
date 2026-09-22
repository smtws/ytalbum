#!/usr/bin/env python3
"""Debug live track count processing"""

import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.core.state_manager import StateManager

async def debug_live_track_count():
    """Search for the playlist and see where track count gets corrupted"""
    
    try:
        # Initialize state manager
        state_manager = StateManager()
        
        # Clear any existing results
        await state_manager.clear_state()
        
        # Search for the specific playlist
        query = "sabaton legends playlist"
        print(f"=== SEARCHING FOR: {query} ===")
        
        await state_manager.start_search(query)
        
        # Wait a bit for initial results
        await asyncio.sleep(5)
        
        print(f"\n=== INITIAL RESULTS ===")
        print(f"Total results: {len(state_manager.state.results)}")
        
        for i, result in enumerate(state_manager.state.results):
            if "legends" in result.title.lower() or "PLA_zjX3swAf7HU2UTseAFBKlT2rxzYlGM" in result.youtube_url:
                print(f"\nResult {i+1} (LEGENDS PLAYLIST):")
                print(f"  ID: {result.id}")
                print(f"  URL: {result.youtube_url}")
                print(f"  Title: {result.title}")
                print(f"  YouTube Track Count: {result.track_count}")
                print(f"  Type: {'playlist' if 'playlist' in result.youtube_url else 'video'}")
                
                # Check if this gets the right count
                if result.track_count != 7 and "PLA_zjX3swAf7HU2UTseAFBKlT2rxzYlGM" in result.youtube_url:
                    print(f"  *** BUG: Expected 7, got {result.track_count} ***")
                    
                break
        
        print(f"\n=== ALL RESULTS TRACK COUNTS ===")
        for i, result in enumerate(state_manager.state.results[:10]):  # First 10 results
            print(f"  {i+1}. Track Count: {result.track_count} | Title: {result.title[:50]}...")
            
    except Exception as e:
        print(f"Debug error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug_live_track_count())