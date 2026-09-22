#!/usr/bin/env python3
"""Clear all backend caches"""

import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.core.state_manager import StateManager

async def clear_all_caches():
    """Clear all backend caches"""
    
    try:
        # Initialize state manager
        state_manager = StateManager()
        
        print("=== CLEARING ALL BACKEND CACHES ===")
        
        # Clear verification cache
        if hasattr(state_manager, 'verification_cache'):
            state_manager.verification_cache.cache.clear()
            print("✓ Cleared verification cache")
            
        # Clear metadata cache  
        if hasattr(state_manager, 'metadata_cache'):
            state_manager.metadata_cache.cache.clear()
            print("✓ Cleared metadata cache")
            
        # Clear normalization service cache if it exists
        if hasattr(state_manager, 'normalization_service') and hasattr(state_manager.normalization_service, 'cache'):
            state_manager.normalization_service.cache.clear()
            print("✓ Cleared normalization cache")
            
        # Clear MusicBrainz service cache if it exists  
        if hasattr(state_manager, 'musicbrainz_service') and hasattr(state_manager.musicbrainz_service, 'cache'):
            state_manager.musicbrainz_service.cache.clear()
            print("✓ Cleared MusicBrainz cache")
            
        # Clear all results
        await state_manager.clear_state()
        print("✓ Cleared all results/state")
        
        print("\n🎯 All backend caches cleared successfully!")
        
    except Exception as e:
        print(f"Error clearing caches: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(clear_all_caches())