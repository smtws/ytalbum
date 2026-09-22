#!/usr/bin/env python3
"""Debug script to check current track count values in live state"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.core.state_manager import StateManager

def debug_track_counts():
    """Debug current track count values"""
    try:
        # Initialize state manager
        state_manager = StateManager()
        
        print("=== TRACK COUNT DEBUG ===")
        print(f"Total results: {len(state_manager.state.results)}")
        
        if not state_manager.state.results:
            print("No results found - try running a search first")
            return
            
        for i, result in enumerate(state_manager.state.results[:5]):  # Show first 5
            print(f"\nResult {i+1}:")
            print(f"  ID: {result.id}")
            print(f"  Title: {result.title[:50]}...")
            print(f"  YouTube Track Count: {result.track_count}")
            
            # Check normalized data
            if result.normalized:
                norm_track_count = result.normalized.get('track_count')
                print(f"  Normalized Track Count: {norm_track_count}")
            
            # Check metadata
            if result.metadata:
                print(f"  Has Metadata: Yes")
                if 'normalized' in result.metadata:
                    meta_track_count = result.metadata['normalized'].get('track_count')
                    print(f"  MusicBrainz Track Count: {meta_track_count}")
                else:
                    print(f"  MusicBrainz Track Count: No normalized metadata")
            else:
                print(f"  Has Metadata: No")
                
        # Show any that have the specific values 46 and 11
        count_46 = [r for r in state_manager.state.results if r.track_count == 46]
        count_11 = [r for r in state_manager.state.results if (r.metadata and 
                    r.metadata.get('normalized', {}).get('track_count') == 11)]
                    
        print(f"\n=== SPECIFIC VALUES ===")
        print(f"Results with track_count=46: {len(count_46)}")
        print(f"Results with MB track_count=11: {len(count_11)}")
        
    except Exception as e:
        print(f"Debug error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_track_counts()