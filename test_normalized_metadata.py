#!/usr/bin/env python3
import asyncio
import json
from backend.services.musicbrainz_service import MusicBrainzService
from backend.core.models import Config

async def test_normalized_metadata_interface():
    """Test the normalized metadata container in MusicBrainz results"""
    
    # Create config with country priorities
    config = Config()
    service = MusicBrainzService(config)
    
    test_albums = [
        ("Sabaton", "Carolus Rex"),
        ("Metallica", "Master of Puppets"), 
        ("Sabaton", "The Art of War")
    ]
    
    for artist, album in test_albums:
        print(f"\n{'='*60}")
        print(f"Testing Normalized Metadata: {artist} - {album}")
        print(f"{'='*60}")
        
        result = await service.search_release(artist, album)
        
        if result:
            print(f"✓ MusicBrainz result found for {artist} - {album}")
            
            # Test raw metadata structure
            print("\n--- Raw Metadata Structure ---")
            print(f"Service: {result.get('service')}")
            print(f"ID: {result.get('id')}")
            print(f"Retrieved at: {result.get('retrieved_at')}")
            print(f"Has cover art URLs: {'cover_art_urls' in result}")
            
            # Test normalized metadata interface
            if 'normalized' in result:
                print("\n--- Normalized Interface ---")
                normalized = result['normalized']
                
                # Core metadata
                print("Core Metadata:")
                print(f"  Title: {normalized.get('title')}")
                print(f"  Artist: {normalized.get('artist')}")
                print(f"  Release Date: {normalized.get('release_date')}")
                print(f"  Release Year: {normalized.get('release_year')}")
                print(f"  Track Count: {normalized.get('track_count')}")
                print(f"  Country: {normalized.get('country')}")
                
                # Cover art interface
                print("\nCover Art Interface:")
                cover_art = normalized.get('cover_art', {})
                print(f"  Available: {cover_art.get('available')}")
                print(f"  Front Cover: {cover_art.get('front_cover')}")
                print(f"  Thumbnail: {cover_art.get('thumbnail')}")
                if cover_art.get('urls'):
                    print(f"  Total URLs: {len(cover_art['urls'])}")
                
                # Service extensions
                print("\nService Extensions:")
                extensions = normalized.get('extensions', {})
                if 'musicbrainz' in extensions:
                    mb_ext = extensions['musicbrainz']
                    print(f"  MusicBrainz MBID: {mb_ext.get('mbid')}")
                    print(f"  Release Status: {mb_ext.get('status')}")
                    print(f"  Primary Type: {mb_ext.get('primary_type')}")
                    print(f"  Secondary Types: {mb_ext.get('secondary_types')}")
                
                # Verify unified interface compatibility
                print("\n--- Interface Compatibility Test ---")
                required_fields = ['title', 'artist', 'release_year', 'track_count', 'cover_art', 'extensions']
                missing_fields = [field for field in required_fields if field not in normalized]
                
                if not missing_fields:
                    print("✓ All required unified interface fields present")
                else:
                    print(f"✗ Missing fields: {missing_fields}")
                
                # Test cover art unified access
                if normalized['cover_art']['available']:
                    print("✓ Cover art accessible through unified interface")
                    print(f"  Frontend can use: result['normalized']['cover_art']['thumbnail']")
                else:
                    print("- No cover art available for this release")
                
                # Show JSON structure for frontend reference
                print("\n--- Frontend Usage Example ---")
                print("// Frontend can access normalized data like this:")
                print(f"const title = result.metadata.normalized.title; // '{normalized.get('title')}'")
                print(f"const artist = result.metadata.normalized.artist; // '{normalized.get('artist')}'")
                print(f"const year = result.metadata.normalized.release_year; // '{normalized.get('release_year')}'")
                print(f"const tracks = result.metadata.normalized.track_count; // {normalized.get('track_count')}")
                if normalized['cover_art']['available']:
                    print(f"const coverUrl = result.metadata.normalized.cover_art.thumbnail;")
                
            else:
                print("✗ No normalized interface found in metadata!")
                
        else:
            print(f"✗ No MusicBrainz result found for {artist} - {album}")
    
    await service.close()
    print(f"\n{'='*60}")
    print("Normalized Metadata Interface Test Complete")
    print(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(test_normalized_metadata_interface())