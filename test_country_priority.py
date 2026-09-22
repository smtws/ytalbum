#!/usr/bin/env python3
import asyncio
from backend.services.musicbrainz_service import MusicBrainzService
from backend.core.models import Config

async def test_country_prioritization():
    # Create config with custom country priorities
    config = Config()
    config.release_country_priority = {
        "US": 100,    # Highest priority
        "DE": 90,     # High priority  
        "GB": 85,     # High priority
        "XE": 80,     # Europe
        "SE": 45,     # Sweden (for Sabaton)
        "MX": 10,     # Low priority (should avoid Mexico release)
    }
    
    service = MusicBrainzService(config)
    
    # Test Carolus Rex - should now prefer US/DE/GB over Mexico
    print("=== Testing Country Prioritization for Carolus Rex ===")
    result = await service.search_release("Sabaton", "Carolus Rex")
    
    if result:
        print(f"✓ Selected release: {result['id']}")
        print(f"  Country: {result.get('country', 'Unknown')}")
        print(f"  Date: {result.get('date', 'Unknown')}")
        
        if 'cover_art_urls' in result:
            print("  ✓ Cover art URLs found!")
            for size, url in result['cover_art_urls'].items():
                print(f"    {size}: {url}")
        else:
            print("  ✗ No cover art URLs")
    else:
        print("✗ No MusicBrainz result found")
    
    await service.close()

if __name__ == "__main__":
    asyncio.run(test_country_prioritization())