#!/usr/bin/env python3
"""Debug channel search raw yt-dlp data"""

import sys
import os
import asyncio
import json
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.services.ytdlp_rate_limiter import YTDLPRateLimiter

async def debug_channel_search_raw():
    """Test what channel search gets from yt-dlp for Sabaton channel"""
    
    # This is what channel_search does
    ytdlp_limiter = YTDLPRateLimiter()
    
    try:
        # Simulate channel search - get Sabaton channel data
        cmd = [
            "yt-dlp", 
            "--flat-playlist",
            "--dump-json",
            "--extractor-args", "youtube:tab_types=playlists,videos",
            "--playlist-items", "1:20",  # Limit to first 20 items
            "https://www.youtube.com/@Sabaton/playlists"
        ]
        
        print(f"=== DEBUGGING CHANNEL SEARCH RAW DATA ===")
        print(f"Running: {' '.join(cmd)}")
        
        stdout, stderr, returncode = await ytdlp_limiter.execute_subprocess(cmd)
        
        print(f"Return code: {returncode}")
        if stderr:
            print(f"STDERR: {stderr}")
            
        if stdout:
            print(f"\n=== RAW YTDLP OUTPUT ===")
            lines = stdout.strip().split('\n')
            print(f"Total lines: {len(lines)}")
            
            target_playlist = "PLA_zjX3swAf7HU2UTseAFBKlT2rxzYlGM"
            
            for i, line in enumerate(lines):
                try:
                    data = json.loads(line)
                    
                    # Check if this is our target playlist
                    if target_playlist in str(data.get('webpage_url', '')):
                        print(f"\n🎯 FOUND TARGET PLAYLIST (Line {i+1}):")
                        print(f"  URL: {data.get('webpage_url')}")
                        print(f"  Title: {data.get('title')}")
                        print(f"  playlist_count: {data.get('playlist_count')}")
                        print(f"  _type: {data.get('_type')}")
                        
                        # Show all count-related fields
                        count_fields = {k: v for k, v in data.items() if 'count' in k.lower()}
                        print(f"  All count fields: {count_fields}")
                        
                        # Show full JSON structure
                        print(f"\n📊 FULL JSON DATA:")
                        print(json.dumps(data, indent=2)[:1000] + "..." if len(json.dumps(data, indent=2)) > 1000 else json.dumps(data, indent=2))
                        break
                        
                except json.JSONDecodeError:
                    continue
                    
        else:
            print("No stdout data")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug_channel_search_raw())