#!/usr/bin/env python3
"""Test Sabaton search with track count fix"""

import asyncio
import json
import requests
import time

async def test_sabaton_search():
    print("=== TESTING SABATON SEARCH WITH TRACK COUNT FIX ===")
    
    # Start search
    response = requests.post("http://localhost:7002/search", 
                           json={"query": "Sabaton"})
    
    if response.status_code != 200:
        print(f"Search request failed: {response.status_code}")
        return
    
    print("✓ Search started")
    
    # Wait for results
    print("⏳ Waiting for results...")
    await asyncio.sleep(10)  # Wait longer for all services
    
    # Get results
    response = requests.get("http://localhost:7002/state")
    if response.status_code != 200:
        print(f"Failed to get state: {response.status_code}")
        return
    
    state = response.json()
    results = state.get("results", [])
    
    print(f"📊 Total results: {len(results)}")
    
    # Look for the Legends playlist specifically
    legends_playlist = None
    legends_count = 0
    
    for result in results:
        if "PLA_zjX3swAf7HU2UTseAFBKlT2rxzYlGM" in result["youtube_url"]:
            legends_playlist = result
            legends_count += 1
    
    if legends_playlist:
        print(f"\n🎵 FOUND LEGENDS PLAYLIST:")
        print(f"   Title: {legends_playlist['title']}")
        print(f"   Track count: {legends_playlist['track_count']}")
        print(f"   Channel: {legends_playlist['channel']}")
        print(f"   Discovered by: {legends_playlist['discovered_by']}")
        print(f"   URL: {legends_playlist['youtube_url']}")
        
        if legends_playlist['track_count'] == 7:
            print("✅ Track count is CORRECT (7)")
        else:
            print(f"❌ Track count is WRONG: {legends_playlist['track_count']} (should be 7)")
    else:
        print("\n❌ Legends playlist not found in results")
    
    # Show track count summary
    track_counts = {}
    for result in results:
        count = result["track_count"]
        track_counts[count] = track_counts.get(count, 0) + 1
    
    print(f"\n📊 TRACK COUNT DISTRIBUTION:")
    for count, num_results in sorted(track_counts.items()):
        print(f"   {count} tracks: {num_results} results")

if __name__ == "__main__":
    asyncio.run(test_sabaton_search())