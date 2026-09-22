#!/usr/bin/env python3
"""
Simple test for deduplication with new metadata format
"""

import sys
from backend.core.models import Result
from backend.services.youtube_deduplicator import YouTubeDeduplicator


def test_merge_functionality():
    """Test the merge function preserves both old and new formats"""
    print("=== Testing Merge Functionality ===")
    
    # Create existing result with old format
    existing_result = Result(
        youtube_url="https://www.youtube.com/playlist?list=PLtest123",
        youtube_id="playlist:PLtest123",
        title="Sabaton - The Great War",
        artist="Sabaton",
        channel="Sabaton Official",
        track_count=10
    )
    
    # Add old format metadata
    existing_result.verification_metadata = {
        "normalized_title": "the great war",
        "normalized_artist": "sabaton", 
        "mb_title": "The Great War",
        "mb_artist": "Sabaton",
        "id": "test-id-123"
    }
    
    # Create new result with new format and better quality (more tracks)
    new_result = Result(
        youtube_url="https://www.youtube.com/playlist?list=PLtest123",
        youtube_id="playlist:PLtest123",
        title="Sabaton - The Great War [FULL ALBUM]",
        artist="Sabaton", 
        channel="Nuclear Blast Records",
        track_count=15  # Better quality
    )
    
    # Add new format metadata
    new_result.normalized = {
        "title": "the great war",
        "artist": "sabaton"
    }
    new_result.metadata = {
        "mb_title": "The Great War",
        "mb_artist": "Sabaton", 
        "id": "test-id-456"
    }
    
    print(f"Existing result: track_count={existing_result.track_count}")
    print(f"  verification_metadata: {bool(existing_result.verification_metadata)}")
    print(f"  normalized: {bool(existing_result.normalized)}")
    print(f"  metadata: {bool(existing_result.metadata)}")
    
    print(f"\nNew result: track_count={new_result.track_count}")
    print(f"  verification_metadata: {bool(new_result.verification_metadata)}")
    print(f"  normalized: {bool(new_result.normalized)}")
    print(f"  metadata: {bool(new_result.metadata)}")
    
    # Test merge
    merged = YouTubeDeduplicator.merge_with_existing_metadata(new_result, existing_result)
    
    print(f"\nMerged result:")
    print(f"  track_count: {merged.track_count}")
    print(f"  verification_metadata: {bool(merged.verification_metadata)}")
    print(f"  normalized: {bool(merged.normalized)}")
    print(f"  metadata: {bool(merged.metadata)}")
    
    # Check preservation
    old_preserved = bool(merged.verification_metadata and 'normalized_title' in merged.verification_metadata)
    new_preserved = bool(merged.normalized and merged.metadata)
    
    print(f"\nOld format preserved: {old_preserved}")
    print(f"New format preserved: {new_preserved}")
    
    if old_preserved and new_preserved:
        print("✅ Both formats preserved during merge")
        return True
    else:
        print("❌ Some formats lost during merge")
        return False


def test_quality_comparison():
    """Test that quality comparison works correctly"""
    print("\n=== Testing Quality Comparison ===")
    
    # Result with old format metadata (lower track count)
    old_format = Result(
        youtube_url="https://www.youtube.com/playlist?list=PLtest123",
        youtube_id="playlist:PLtest123",
        title="Test Album",
        artist="Test Artist",
        channel="Test Channel",
        track_count=10
    )
    old_format.verification_metadata = {"normalized_title": "test album"}
    
    # Result with new format metadata (higher track count)
    new_format = Result(
        youtube_url="https://www.youtube.com/playlist?list=PLtest123", 
        youtube_id="playlist:PLtest123",
        title="Test Album [FULL]",
        artist="Test Artist",
        channel="Test Channel Official",
        track_count=15
    )
    new_format.normalized = {"title": "test album"}
    new_format.metadata = {"mb_title": "Test Album"}
    
    is_better = YouTubeDeduplicator._is_better_quality(new_format, old_format)
    print(f"New format result is better quality: {is_better}")
    
    if is_better:
        print("✅ Quality comparison works correctly")
        return True
    else:
        print("❌ Quality comparison failed")
        return False


def main():
    """Run tests"""
    print("Testing deduplication with new metadata format...\n")
    
    test1 = test_merge_functionality()
    test2 = test_quality_comparison()
    
    print(f"\n=== Results ===")
    print(f"Merge test: {'✅ PASSED' if test1 else '❌ FAILED'}")
    print(f"Quality test: {'✅ PASSED' if test2 else '❌ FAILED'}")
    
    if test1 and test2:
        print("\n🎉 All tests passed! Migration is working.")
        return 0
    else:
        print("\n❌ Some tests failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())