#!/usr/bin/env python3
import json
import asyncio
from backend.services.musicbrainz_service import MusicBrainzService

async def test_metadata():
    service = MusicBrainzService()
    
    # Test search for Sabaton - Legends (should have cover art)
    result = await service.search_release("Sabaton", "Legends")
    
    if result:
        print("MusicBrainz raw metadata:")
        print(json.dumps(result, indent=2, default=str))
        
        if 'cover_art_urls' in result:
            print("\nCover art URLs found:")
            for size, url in result['cover_art_urls'].items():
                print(f"  {size}: {url}")
        else:
            print("\nNo cover art URLs in metadata!")
    else:
        print("No MusicBrainz result found!")
    
    await service.close()

if __name__ == "__main__":
    asyncio.run(test_metadata())