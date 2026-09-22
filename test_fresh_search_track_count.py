#!/usr/bin/env python3
"""Test fresh search to verify track count fix"""

import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.core.state_manager import StateManager

async def test_fresh_search():
    """Perform a fresh search for Sabaton Legends playlist"""
    
    try:
        # Initialize state manager
        state_manager = StateManager()
        
        # Clear all results first
        await state_manager.clear_state()
        print("✓ Cleared all existing results")
        
        # Search specifically for the Legends playlist URL
        query = "https://www.youtube.com/playlist?list=PLA_zjX3swAf7HU2UTseAFBKlT2rxzYlGM"
        print(f"\n=== SEARCHING FOR DIRECT PLAYLIST URL ===")
        print(f"Query: {query}")
        print(f"Expected track count: 7")
        
        # Start search
        await state_manager.start_search(query)
        
        # Wait for results
        print("⏳ Waiting for search results...")
        await asyncio.sleep(8)  # Wait longer for search to complete
        
        print(f"\n=== FRESH SEARCH RESULTS ===")
        print(f"Total results: {len(state_manager.state.results)}")
        
        legends_found = False
        for i, result in enumerate(state_manager.state.results):
            print(f"\nResult {i+1}:")
            print(f"  Title: {result.title}")
            print(f"  Track Count: {result.track_count}")
            print(f"  URL: {result.youtube_url}")
            
            # Check if this is the Legends playlist
            if "PLA_zjX3swAf7HU2UTseAFBKlT2rxzYlGM" in result.youtube_url or "legends" in result.title.lower():
                legends_found = True
                print(f"  *** LEGENDS PLAYLIST FOUND ***")
                
                if result.track_count == 7:
                    print(f"  ✅ TRACK COUNT CORRECT: {result.track_count}")
                else:
                    print(f"  ❌ TRACK COUNT WRONG: {result.track_count} (expected 7)")
                    
                    # Show normalized and metadata info
                    if hasattr(result, 'normalized') and result.normalized:
                        print(f"  Normalized: {result.normalized}")
                    if hasattr(result, 'metadata') and result.metadata:
                        print(f"  Metadata keys: {list(result.metadata.keys()) if result.metadata else 'None'}")
                        if 'normalized' in result.metadata:
                            mb_count = result.metadata['normalized'].get('track_count', 'None')
                            print(f"  MusicBrainz track count: {mb_count}")
                            
        if not legends_found:
            print(f"\n❌ LEGENDS PLAYLIST NOT FOUND IN RESULTS")
            
    except Exception as e:
        print(f"Test error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_fresh_search())