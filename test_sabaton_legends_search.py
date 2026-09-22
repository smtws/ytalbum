#!/usr/bin/env python3
"""Test search for Sabaton Legends"""

import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.core.state_manager import StateManager

async def test_sabaton_legends():
    """Search for Sabaton Legends to verify track count fix"""
    
    try:
        # Initialize state manager
        state_manager = StateManager()
        
        # Clear all results first
        await state_manager.clear_state()
        print("✓ Cleared all existing results")
        
        # Search for Sabaton Legends
        query = "sabaton legends"
        print(f"\n=== SEARCHING FOR: {query} ===")
        print(f"Looking for playlist with 7 tracks")
        
        # Start search
        await state_manager.start_search(query)
        
        # Wait for initial results
        print("⏳ Waiting for search results...")
        await asyncio.sleep(5)
        
        print(f"\n=== SEARCH RESULTS ===")
        print(f"Total results: {len(state_manager.state.results)}")
        
        # Look through results for anything Legends-related
        for i, result in enumerate(state_manager.state.results[:10]):  # First 10 results
            print(f"\nResult {i+1}:")
            print(f"  Title: {result.title[:60]}...")
            print(f"  Track Count: {result.track_count}")
            print(f"  URL: {result.youtube_url}")
            
            # Check if this might be the Legends playlist
            if "legends" in result.title.lower():
                print(f"  *** POTENTIAL LEGENDS MATCH ***")
                if result.track_count:
                    print(f"  Track count status: {'✅ REASONABLE' if result.track_count < 20 else '❌ TOO HIGH'}")
                    
        # Wait longer for processing to complete (normalization, MusicBrainz)
        print(f"\n⏳ Waiting for processing to complete...")
        await asyncio.sleep(10)
        
        print(f"\n=== AFTER PROCESSING ===")
        print(f"Total results: {len(state_manager.state.results)}")
        
        for i, result in enumerate(state_manager.state.results[:10]):
            if "legends" in result.title.lower():
                print(f"\nLegends Result {i+1}:")
                print(f"  Title: {result.title}")
                print(f"  YouTube Track Count: {result.track_count}")
                
                # Check metadata if available
                if hasattr(result, 'metadata') and result.metadata:
                    if 'normalized' in result.metadata:
                        mb_count = result.metadata['normalized'].get('track_count')
                        print(f"  MusicBrainz Track Count: {mb_count}")
                        
                        # This is where the MB:11/YT:46 display comes from
                        print(f"  Display would show: MB:{mb_count} / YT:{result.track_count}")
                        
    except Exception as e:
        print(f"Test error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_sabaton_legends())