#!/usr/bin/env python3
"""Test script to search and examine track count values"""

import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.core.state_manager import StateManager

async def test_search_track_counts():
    """Test a search and examine track count values"""
    try:
        # Initialize state manager
        state_manager = StateManager()
        
        # Clear any existing results
        await state_manager.clear_state()
        
        # Run a search for something likely to have track counts
        print("=== STARTING SEARCH FOR 'sabaton the war to end all wars' ===")
        await state_manager.start_search("sabaton the war to end all wars")
        
        # Wait a bit for results
        await asyncio.sleep(3)
        
        print(f"\n=== SEARCH RESULTS ===")
        print(f"Total results: {len(state_manager.state.results)}")
        
        if state_manager.state.results:
            for i, result in enumerate(state_manager.state.results[:3]):  # Show first 3
                print(f"\nResult {i+1}:")
                print(f"  ID: {result.id}")
                print(f"  Title: {result.title[:60]}...")
                print(f"  Artist: {result.artist}")
                print(f"  YouTube Track Count: {result.track_count}")
                
                # Check normalized data
                if hasattr(result, 'normalized') and result.normalized:
                    norm_track_count = result.normalized.get('track_count')
                    print(f"  Normalized Track Count: {norm_track_count}")
                
                # Check metadata
                if hasattr(result, 'metadata') and result.metadata:
                    print(f"  Has Metadata: Yes")
                    if 'normalized' in result.metadata:
                        meta_track_count = result.metadata['normalized'].get('track_count')
                        print(f"  MusicBrainz Track Count: {meta_track_count}")
                    else:
                        print(f"  MusicBrainz Track Count: No normalized metadata")
                else:
                    print(f"  Has Metadata: No")
                    
        # Wait for processing to complete
        print("\nWaiting for processing to complete...")
        await asyncio.sleep(10)
        
        print(f"\n=== AFTER PROCESSING ===")
        print(f"Total results: {len(state_manager.state.results)}")
        
        for i, result in enumerate(state_manager.state.results[:3]):  # Show first 3
            print(f"\nResult {i+1} (After Processing):")
            print(f"  ID: {result.id}")
            print(f"  Title: {result.title[:60]}...")
            print(f"  YouTube Track Count: {result.track_count}")
            
            # Check normalized data
            if hasattr(result, 'normalized') and result.normalized:
                norm_track_count = result.normalized.get('track_count')
                print(f"  Normalized Track Count: {norm_track_count}")
            
            # Check metadata
            if hasattr(result, 'metadata') and result.metadata:
                print(f"  Has Metadata: Yes")
                if 'normalized' in result.metadata:
                    meta_track_count = result.metadata['normalized'].get('track_count')
                    print(f"  MusicBrainz Track Count: {meta_track_count}")
                else:
                    print(f"  MusicBrainz Track Count: No normalized metadata")
            else:
                print(f"  Has Metadata: No")
                
    except Exception as e:
        print(f"Test error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_search_track_counts())