#!/usr/bin/env python3
"""Debug current results in the system"""

import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.core.state_manager import StateManager

async def debug_current_results():
    """Check what results are currently in the system"""
    
    try:
        # Initialize state manager
        state_manager = StateManager()
        
        print(f"=== CURRENT RESULTS IN SYSTEM ===")
        print(f"Total results: {len(state_manager.state.results)}")
        
        for i, result in enumerate(state_manager.state.results):
            print(f"\nResult {i+1}:")
            print(f"  ID: {result.id}")
            print(f"  URL: {result.youtube_url}")
            print(f"  Title: {result.title}")
            print(f"  Artist: {result.artist}")
            print(f"  YouTube Track Count: {result.track_count}")
            
            # Check for the specific Legends playlist
            if "legends" in result.title.lower() or "PLA_zjX3swAf7HU2UTseAFBKlT2rxzYlGM" in result.youtube_url:
                print(f"  *** THIS IS THE LEGENDS PLAYLIST ***")
                print(f"  Expected: 7 tracks")
                print(f"  Actual: {result.track_count} tracks")
                
                if result.track_count != 7:
                    print(f"  *** BUG CONFIRMED: Wrong track count! ***")
                    
                    # Let's trace where this number might be coming from
                    print(f"  Normalized data: {result.normalized}")
                    print(f"  Metadata: {result.metadata}")
                    
                    # Check if metadata has track_count
                    if result.metadata and 'normalized' in result.metadata:
                        mb_track_count = result.metadata['normalized'].get('track_count')
                        print(f"  MusicBrainz track count: {mb_track_count}")
                        
        print(f"\n=== CLEARING ALL RESULTS ===")
        await state_manager.clear_state()
        print(f"Results cleared. Now: {len(state_manager.state.results)} results")
                    
    except Exception as e:
        print(f"Debug error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug_current_results())