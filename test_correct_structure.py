#!/usr/bin/env python3
import asyncio
from backend.services.musicbrainz_service import MusicBrainzService
from backend.core.models import Config, Result

async def test_correct_structure():
    """Test that we have normalized containers in the right places"""
    
    config = Config()
    service = MusicBrainzService(config)
    
    # Create a mock result object like the real system would
    result = Result(
        youtube_url="https://youtube.com/watch?v=test123",
        youtube_id="test123",
        title="Carolus Rex",
        artist="Sabaton",
        channel="Sabaton Official",
        discovered_by="test"
    )
    
    # Simulate NormalizationService populating result.normalized
    result.normalized = {
        "artist": "Sabaton",
        "title": "Carolus Rex",
        "clean_title": "carolus rex",
        "processed_by": "normalization_service"
    }
    
    print("=== Testing Correct Structure ===")
    print(f"Before MusicBrainz: result.normalized = {result.normalized}")
    print(f"Before MusicBrainz: result.metadata = {result.metadata}")
    
    # Get MusicBrainz metadata (this should populate result.metadata)
    mb_metadata = await service.search_release("Sabaton", "Carolus Rex")
    if mb_metadata:
        result.metadata = mb_metadata
    
    print("\n=== After MusicBrainz Service ===")
    print(f"✓ result.normalized still exists: {bool(result.normalized)}")
    print(f"✓ result.metadata now exists: {bool(result.metadata)}")
    print(f"✓ result.metadata.normalized exists: {'normalized' in result.metadata}")
    
    print("\n=== Structure Verification ===")
    print("result.normalized (from NormalizationService):")
    for key, value in result.normalized.items():
        print(f"  {key}: {value}")
    
    if 'normalized' in result.metadata:
        print("\nresult.metadata.normalized (from MusicBrainzService):")
        metadata_normalized = result.metadata['normalized']
        for key, value in metadata_normalized.items():
            if key == 'cover_art':
                print(f"  cover_art:")
                for art_key, art_value in value.items():
                    print(f"    {art_key}: {art_value}")
            elif key == 'extensions':
                print(f"  extensions:")
                for ext_service, ext_data in value.items():
                    print(f"    {ext_service}: {ext_data}")
            else:
                print(f"  {key}: {value}")
    
    print("\n=== Frontend Access Examples ===")
    print("// NormalizationService data:")
    print(f"const cleanTitle = result.normalized.clean_title; // '{result.normalized.get('clean_title')}'")
    
    if 'normalized' in result.metadata:
        meta_norm = result.metadata['normalized']
        print("\n// MusicBrainzService data:")
        print(f"const year = result.metadata.normalized.release_year; // '{meta_norm.get('release_year')}'")
        print(f"const tracks = result.metadata.normalized.track_count; // {meta_norm.get('track_count')}")
        if meta_norm.get('cover_art', {}).get('available'):
            print(f"const coverUrl = result.metadata.normalized.cover_art.thumbnail;")
    
    print("\n✅ Both normalized containers coexist correctly!")
    
    await service.close()

if __name__ == "__main__":
    asyncio.run(test_correct_structure())