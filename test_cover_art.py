#!/usr/bin/env python3
import json
import asyncio
from backend.services.musicbrainz_service import MusicBrainzService

async def test_multiple_albums():
    service = MusicBrainzService()
    
    test_albums = [
        ("Sabaton", "Primo Victoria"),
        ("Sabaton", "Carolus Rex"), 
        ("Sabaton", "The Art of War"),
        ("Sabaton", "Heroes"),
        ("Metallica", "Master of Puppets")
    ]
    
    for artist, album in test_albums:
        print(f"\n=== Testing {artist} - {album} ===")
        result = await service.search_release(artist, album)
        
        if result:
            if 'cover_art_urls' in result:
                print("✓ Cover art URLs found!")
                for size, url in result['cover_art_urls'].items():
                    print(f"  {size}: {url}")
            else:
                print("✗ No cover art URLs")
                # Check if coverartarchive data exists
                if 'coverartarchive' in result:
                    print(f"  coverartarchive: {result['coverartarchive']}")
                else:
                    print("  No coverartarchive field")
        else:
            print("✗ No MusicBrainz result found")
    
    await service.close()

if __name__ == "__main__":
    asyncio.run(test_multiple_albums())