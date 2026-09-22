#!/usr/bin/env python3
"""
Debug track count fixing in YouTubeDeduplicator
"""

import asyncio
import subprocess
import json
from backend.services.youtube_deduplicator import YouTubeDeduplicator
from backend.core.models import Result

async def execute_ytdlp_command(cmd):
    """Simple ytdlp executor for testing"""
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        return stdout.decode('utf-8'), stderr.decode('utf-8'), process.returncode
    except Exception as e:
        print(f"Command execution failed: {e}")
        return "", str(e), 1

async def test_track_count_fix():
    print("=== TESTING TRACK COUNT FIX ===")
    
    # Test the specific Sabaton Legends playlist
    playlist_url = "https://www.youtube.com/playlist?list=PLA_zjX3swAf7HU2UTseAFBKlT2rxzYlGM"
    
    # Create a result with incorrect track count (like from search services)
    result = Result(
        youtube_url=playlist_url,
        youtube_id="playlist:PLA_zjX3swAf7HU2UTseAFBKlT2rxzYlGM",
        title="SABATON - Legends",
        artist="sabaton",
        channel="Sabaton",
        discovered_by="test",
        track_count=1  # Incorrect - should be 7
    )
    
    print(f"Original track count: {result.track_count}")
    print(f"YouTube ID: {result.youtube_id}")
    print(f"Is playlist: {result.youtube_id.startswith('playlist:') if result.youtube_id else False}")
    
    # Test the fix
    fixed_result = await YouTubeDeduplicator.fix_playlist_track_count(result, execute_ytdlp_command)
    
    print(f"Fixed track count: {fixed_result.track_count}")
    
    # Also test direct yt-dlp call
    print("\n=== DIRECT YT-DLP TEST ===")
    cmd = [
        "yt-dlp",
        "--flat-playlist", 
        "--print", "%(playlist_count)s",
        playlist_url
    ]
    
    stdout, stderr, returncode = await execute_ytdlp_command(cmd)
    print(f"yt-dlp command: {' '.join(cmd)}")
    print(f"Return code: {returncode}")
    print(f"stdout: '{stdout.strip()}'")
    print(f"stderr: '{stderr.strip()}'")
    
    if returncode == 0 and stdout.strip().isdigit():
        actual_count = int(stdout.strip())
        print(f"Actual playlist count: {actual_count}")
    else:
        print("Failed to get playlist count")

if __name__ == "__main__":
    asyncio.run(test_track_count_fix())