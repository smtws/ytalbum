#!/usr/bin/env python3
"""Debug Sabaton search to find raw YouTube data for specific playlist"""

import sys
import os
import asyncio
import json
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.core.state_manager import StateManager

async def debug_sabaton_search():
    """Search for Sabaton and extract raw data for the specific playlist"""
    
    target_playlist = "PLA_zjX3swAf7HU2UTseAFBKlT2rxzYlGM"
    target_url = f"https://www.youtube.com/playlist?list={target_playlist}"
    
    try:
        # Initialize state manager
        state_manager = StateManager()
        
        # Clear all results first
        await state_manager.clear_state()
        print("✓ Cleared all existing results")
        
        # Search for Sabaton
        query = "sabaton"
        print(f"\n=== SEARCHING FOR: {query} ===")
        print(f"Looking for playlist: {target_url}")
        print(f"Expected track count: 7")
        
        # Start search
        await state_manager.start_search(query)
        
        # Wait for initial results
        print("⏳ Waiting for initial search results...")
        await asyncio.sleep(8)
        
        print(f"\n=== RAW SEARCH RESULTS ===")
        print(f"Total results: {len(state_manager.state.results)}")
        
        # Look for the specific playlist
        target_found = False
        for i, result in enumerate(state_manager.state.results):
            if target_playlist in result.youtube_url or "legends" in result.title.lower():
                target_found = True
                print(f"\n🎯 FOUND TARGET PLAYLIST (Result {i+1}):")
                print(f"  URL: {result.youtube_url}")
                print(f"  Title: {result.title}")
                print(f"  Artist: {result.artist}")
                print(f"  Channel: {result.channel}")
                print(f"  Track Count: {result.track_count}")
                print(f"  Quality Score: {result.quality_score}")
                print(f"  Thumbnail URL: {result.thumbnail_url}")
                print(f"  Discovered By: {result.discovered_by}")
                print(f"  YouTube ID: {result.youtube_id}")
                
                # Show all raw attributes
                print(f"\n📊 RAW RESULT OBJECT:")
                result_dict = result.model_dump()
                for key, value in result_dict.items():
                    print(f"  {key}: {value}")
                    
                break
        
        if not target_found:
            print(f"\n❌ TARGET PLAYLIST NOT FOUND")
            print(f"Available results:")
            for i, result in enumerate(state_manager.state.results[:5]):
                print(f"  {i+1}. {result.title[:50]}... | Track Count: {result.track_count}")
        
        # Wait longer for processing
        print(f"\n⏳ Waiting for processing (normalization, MusicBrainz)...")
        await asyncio.sleep(10)
        
        print(f"\n=== AFTER PROCESSING ===")
        
        # Check the target again after processing
        for i, result in enumerate(state_manager.state.results):
            if target_playlist in result.youtube_url or "legends" in result.title.lower():
                print(f"\n🎯 TARGET AFTER PROCESSING (Result {i+1}):")
                print(f"  YouTube Track Count: {result.track_count}")
                
                # Check normalized data
                if hasattr(result, 'normalized') and result.normalized:
                    print(f"  Normalized: {result.normalized}")
                else:
                    print(f"  Normalized: None")
                
                # Check metadata
                if hasattr(result, 'metadata') and result.metadata:
                    print(f"  Metadata Keys: {list(result.metadata.keys())}")
                    if 'normalized' in result.metadata:
                        mb_count = result.metadata['normalized'].get('track_count')
                        print(f"  MusicBrainz Track Count: {mb_count}")
                        print(f"  Full MusicBrainz normalized: {result.metadata['normalized']}")
                    else:
                        print(f"  MusicBrainz normalized: None")
                else:
                    print(f"  Metadata: None")
                
                # Show exactly what the frontend would display
                print(f"\n🖥️ FRONTEND DISPLAY WOULD SHOW:")
                yt_count = result.track_count
                mb_count = result.metadata.get('normalized', {}).get('track_count') if result.metadata else None
                print(f"  YT Count: {yt_count}")
                print(f"  MB Count: {mb_count}")
                if mb_count and yt_count:
                    print(f"  Display: MB:{mb_count} / YT:{yt_count}")
                else:
                    print(f"  Display: {yt_count} tracks" if yt_count else "No track count")
                
                break
                
    except Exception as e:
        print(f"Debug error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug_sabaton_search())