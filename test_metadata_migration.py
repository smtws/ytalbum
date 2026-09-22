#!/usr/bin/env python3
"""
Test script to verify the migration from verification_metadata to normalized/metadata format
Tests both deduplication and data preservation
"""

import sys
import asyncio
from datetime import datetime
from backend.core.models import Result, AppState
from backend.core.state_manager import StateManager
from backend.services.youtube_deduplicator import YouTubeDeduplicator


async def test_deduplication_with_new_format():
    """Test that deduplication works with both old and new metadata formats"""
    print("=== Testing Deduplication with New Format ===")
    
    # Create a test state manager
    state_manager = StateManager()
    
    # Create test results with different formats
    result1 = Result(
        youtube_url="https://www.youtube.com/playlist?list=PLtest123",
        youtube_id="playlist:PLtest123",
        title="Sabaton - The Great War (Full Album)",
        artist="Sabaton",
        channel="Sabaton Official"
    )
    
    # Simulate old format metadata
    result1.verification_metadata = {
        "normalized_title": "the great war",
        "normalized_artist": "sabaton", 
        "normalized_album": "the great war",
        "mb_title": "The Great War",
        "mb_artist": "Sabaton",
        "id": "test-id-123",
        "service": "musicbrainz"
    }
    
    # Add to state
    state_manager.state.add_result(result1)
    
    # Create a duplicate with new format
    result2 = Result(
        youtube_url="https://www.youtube.com/playlist?list=PLtest123",  # Same URL = duplicate
        youtube_id="playlist:PLtest123", 
        title="Sabaton - The Great War [FULL ALBUM]",
        artist="Sabaton",
        channel="Nuclear Blast Records"
    )
    
    # Simulate new format metadata
    result2.normalized = {
        "title": "the great war",
        "artist": "sabaton",
        "album": "the great war"
    }
    result2.metadata = {
        "mb_title": "The Great War", 
        "mb_artist": "Sabaton",
        "id": "test-id-123",
        "service": "musicbrainz"
    }
    
    print(f"Result 1 (old format): {result1.title}")
    print(f"  verification_metadata: {bool(result1.verification_metadata)}")
    print(f"  normalized: {bool(result1.normalized)}")
    print(f"  metadata: {bool(result1.metadata)}")
    
    print(f"\nResult 2 (new format): {result2.title}")  
    print(f"  verification_metadata: {bool(result2.verification_metadata)}")
    print(f"  normalized: {bool(result2.normalized)}")
    print(f"  metadata: {bool(result2.metadata)}")
    
    # Test deduplication
    existing_results = [result1]
    should_add = YouTubeDeduplicator.should_add_result(result2, existing_results)
    
    print(f"\nShould add duplicate result: {should_add}")
    
    if should_add == "merge":
        print("✅ Deduplication detected - attempting merge")
        
        # Test merge functionality
        merged_result = YouTubeDeduplicator.merge_with_existing_metadata(result2, result1)
        
        print(f"\nMerged result:")
        print(f"  title: {merged_result.title}")
        print(f"  verification_metadata: {bool(merged_result.verification_metadata)}")
        print(f"  normalized: {bool(merged_result.normalized)}")
        print(f"  metadata: {bool(merged_result.metadata)}")
        
        # Check that both formats are preserved
        if merged_result.verification_metadata and merged_result.normalized and merged_result.metadata:
            print("✅ Both old and new formats preserved in merge")
        else:
            print("❌ Missing metadata in merge")
            return False
            
    else:
        print(f"❌ Deduplication failed - got: {should_add}")
        return False
        
    return True


async def test_live_normalization():
    """Test that live normalization populates both formats"""
    print("\n=== Testing Live Normalization ===")
    
    state_manager = StateManager()
    
    # Create a test result
    result = Result(
        youtube_url="https://www.youtube.com/watch?v=BVWfqOSdzs4",
        youtube_id="BVWfqOSdzs4",
        title="Sabaton - Fields of Verdun (Official Music Video)",
        artist="Sabaton",
        channel="Sabaton Official"
    )
    
    # Add to state
    state_manager.state.add_result(result)
    
    print(f"Original result: {result.title}")
    print(f"  verification_metadata: {bool(result.verification_metadata)}")
    print(f"  normalized: {bool(result.normalized)}")
    print(f"  metadata: {bool(result.metadata)}")
    
    # Trigger normalization
    await state_manager.normalize_result(result)
    
    # Give it a moment to process
    await asyncio.sleep(0.2)
    
    # Check updated result
    updated_result = state_manager.state.get_result_by_youtube_id(result.youtube_id)
    
    print(f"\nAfter normalization:")
    print(f"  verification_metadata: {bool(updated_result.verification_metadata)}")
    print(f"  normalized: {bool(updated_result.normalized)}")
    print(f"  metadata: {bool(updated_result.metadata)}")
    
    if updated_result.verification_metadata:
        print(f"  verification_metadata keys: {list(updated_result.verification_metadata.keys())}")
    
    if updated_result.normalized:
        print(f"  normalized keys: {list(updated_result.normalized.keys())}")
        
    # Check both formats are populated
    has_old_format = updated_result.verification_metadata and 'normalized_title' in updated_result.verification_metadata
    has_new_format = updated_result.normalized and 'title' in updated_result.normalized
    
    if has_old_format and has_new_format:
        print("✅ Both old and new formats populated by normalization")
        return True
    else:
        print(f"❌ Missing formats - old: {has_old_format}, new: {has_new_format}")
        return False


async def main():
    """Run all tests"""
    print("Testing metadata format migration...\n")
    
    test1_passed = await test_deduplication_with_new_format()
    test2_passed = await test_live_normalization()
    
    print(f"\n=== Test Results ===")
    print(f"Deduplication test: {'✅ PASSED' if test1_passed else '❌ FAILED'}")
    print(f"Normalization test: {'✅ PASSED' if test2_passed else '❌ FAILED'}")
    
    if test1_passed and test2_passed:
        print("\n🎉 All tests passed! Migration appears successful.")
        return 0
    else:
        print("\n❌ Some tests failed. Check the implementation.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))