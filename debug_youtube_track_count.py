#!/usr/bin/env python3
"""Debug YouTube track count extraction"""

import sys
import os
import asyncio
import json
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.services.ytdlp_rate_limiter import YTDLPRateLimiter

async def test_youtube_track_count():
    """Test what yt-dlp returns for the specific playlist"""
    
    url = "https://www.youtube.com/playlist?list=PLA_zjX3swAf7HU2UTseAFBKlT2rxzYlGM"
    
    print(f"=== TESTING YOUTUBE TRACK COUNT ===")
    print(f"URL: {url}")
    print("Expected: 7 videos")
    
    # Initialize rate limiter
    ytdlp_limiter = YTDLPRateLimiter()
    
    try:
        # Test with flat-playlist to get playlist info
        cmd = [
            "yt-dlp", 
            "--flat-playlist",
            "--print", "%(playlist_count)s",
            "--print", "%(playlist_title)s", 
            "--print", "%(playlist_id)s",
            url
        ]
        
        print(f"\nRunning: {' '.join(cmd)}")
        stdout, stderr, returncode = await ytdlp_limiter.execute_subprocess(cmd)
        
        print(f"\nReturn code: {returncode}")
        print(f"STDOUT:\n{stdout}")
        print(f"STDERR:\n{stderr}")
        
        # Test with JSON output to get full data structure
        cmd_json = [
            "yt-dlp",
            "--flat-playlist", 
            "--dump-json",
            url
        ]
        
        print(f"\n=== FULL JSON DATA ===")
        print(f"Running: {' '.join(cmd_json)}")
        stdout_json, stderr_json, returncode_json = await ytdlp_limiter.execute_subprocess(cmd_json)
        
        print(f"\nReturn code: {returncode_json}")
        if stdout_json:
            try:
                # Parse first line of JSON (playlist info)
                first_line = stdout_json.strip().split('\n')[0]
                data = json.loads(first_line)
                
                print(f"JSON Keys: {list(data.keys())}")
                print(f"playlist_count: {data.get('playlist_count')}")
                print(f"playlist_title: {data.get('playlist_title')}")
                print(f"playlist_id: {data.get('playlist_id')}")
                print(f"_type: {data.get('_type')}")
                
                # Look for other count-related fields
                count_fields = {k: v for k, v in data.items() if 'count' in k.lower()}
                print(f"Count-related fields: {count_fields}")
                
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
                print(f"Raw stdout: {stdout_json[:500]}...")
        
        if stderr_json:
            print(f"STDERR: {stderr_json}")
            
    except Exception as e:
        print(f"Test error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_youtube_track_count())