#!/usr/bin/env python3
"""
YouTube Music Downloader - YouTube Music Search Service
Direct search using YouTube Music's search functionality via yt-dlp
"""

import asyncio
import subprocess
import json
import re
from typing import List, Optional, Dict, Any
from datetime import datetime

from backend.core.models import Result
from backend.services.youtube_deduplicator import YouTubeDeduplicator


class YouTubeMusicSearchService:
    """
    Stateless search service for direct YouTube Music searches
    Uses yt-dlp's ytsearch and ytmusicsearch functionality
    """
    
    def __init__(self):
        self.service_name = "youtube_music_search"    
    async def search(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """
        Search YouTube Music directly for artist albums
        
        Args:
            artist: Artist name to search for
            album: Optional specific album name
            cookie_options: Cookie options from StateManager (single source of truth)
            
        Returns:
            List of Result objects (stateless - doesn't modify state)
        """
        print(f"YouTubeMusicSearchService: Searching for artist '{artist}'" + 
              (f" album '{album}'" if album else ""))
        
        # Use empty dict if no cookie options provided
        if cookie_options is None:
            cookie_options = {}
        
        
        results = []
        
        try:
            # Strategy 1: YouTube Music search for full albums
            music_results = await self._search_youtube_music_albums(artist, album, cookie_options)
            results.extend(music_results)
            
            # Strategy 2: Regular YouTube search with music keywords
            youtube_results = await self._search_youtube_with_music_keywords(artist, album, cookie_options)
            results.extend(youtube_results)
            
            print(f"YouTubeMusicSearchService: Found {len(results)} total results")
            
        except Exception as e:
            print(f"YouTubeMusicSearchService: Search failed: {e}")
        
        return results
    
    async def _search_youtube_music_albums(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Search YouTube Music specifically for albums"""
        results = []
        
        try:
            # Build search query for albums
            if album:
                search_query = f"{artist} {album} album"
            else:
                search_query = f"{artist} album full"
            
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--default-search", "ytmusicsearch20:",  # Search YouTube Music, limit 20
                "--match-filter", "duration > 600",  # Only videos longer than 10 minutes
                search_query
            ]
            
            # Add cookie options
            if "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            # Run command
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            
            if process.returncode == 0 and stdout:
                lines = stdout.decode('utf-8').strip().split('\n')
                
                for line in lines:
                    try:
                        data = json.loads(line)
                        
                        # Filter for likely album content
                        if self._is_likely_album_content(data, artist, album):
                            # Extract thumbnail URL (prefer best quality)
                            thumbnail_url = None
                            if 'thumbnail' in data:
                                thumbnail_url = data['thumbnail']
                            elif 'thumbnails' in data and data['thumbnails']:
                                thumbnail_url = data['thumbnails'][-1].get('url')  # Last is usually best quality
                            
                            # Extract track count (playlist_count for playlists, 1 for single videos)
                            track_count = data.get('playlist_count')
                            if track_count is None:
                                # Single video that passed duration filter (>10min) - assume 1 track
                                track_count = 1
                            
                            result = YouTubeDeduplicator.prepare_result(
                                url=data.get('webpage_url', ''),
                                title=data.get('title', ''),
                                artist=artist,
                                channel=data.get('uploader', 'Unknown'),
                                discovered_by=self.service_name,
                                thumbnail_url=thumbnail_url,
                                track_count=track_count
                            )
                            
                            if result:
                                results.append(result)
                                
                    except json.JSONDecodeError:
                        continue
            
        except Exception as e:
            print(f"YouTubeMusicSearchService: YouTube Music search failed: {e}")
        
        return results
    
    async def _search_youtube_with_music_keywords(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Search regular YouTube with music-specific keywords"""
        results = []
        
        try:
            # Build search queries with music keywords
            queries = []
            
            if album:
                queries = [
                    f"{artist} {album} full album",
                    f"{artist} {album} complete album",
                    f"{artist} {album} album playlist"
                ]
            else:
                queries = [
                    f"{artist} full album",
                    f"{artist} complete discography", 
                    f"{artist} album collection"
                ]
            
            for query in queries:
                query_results = await self._search_single_youtube_query(query, cookie_options)
                results.extend(query_results)
            
        except Exception as e:
            print(f"YouTubeMusicSearchService: YouTube keyword search failed: {e}")
        
        return results
    
    async def _search_single_youtube_query(self, query: str, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Execute a single YouTube search query"""
        results = []
        
        try:
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--default-search", "ytsearch15:",  # Regular YouTube search, limit 15
                "--match-filter", "duration > 600",  # Only videos longer than 10 minutes
                query
            ]
            
            # Add cookie options
            if "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=25)
            
            if process.returncode == 0 and stdout:
                lines = stdout.decode('utf-8').strip().split('\n')
                
                for line in lines:
                    try:
                        data = json.loads(line)
                        
                        # Extract artist from query (first word usually)
                        artist = query.split()[0]
                        
                        result = YouTubeDeduplicator.prepare_result(
                            url=data.get('webpage_url', ''),
                            title=data.get('title', ''),
                            artist=artist,
                            channel=data.get('uploader', 'Unknown'),
                            discovered_by=self.service_name,
                            view_count=data.get('view_count'),
                            duration=data.get('duration')
                        )
                        
                        if result:
                            results.append(result)
                            
                    except json.JSONDecodeError:
                        continue
        
        except Exception as e:
            print(f"YouTubeMusicSearchService: Single query '{query}' failed: {e}")
        
        return results
    
    def _is_likely_album_content(self, data: Dict[str, Any], artist: str, album: Optional[str] = None) -> bool:
        """Check if search result represents album content"""
        title = data.get('title', '').lower()
        uploader = data.get('uploader', '').lower()
        
        # Check artist match
        artist_lower = artist.lower()
        if artist_lower not in title and artist_lower not in uploader:
            return False
        
        # Check for album indicators
        album_keywords = ['full album', 'complete album', 'album', 'playlist', 'discography']
        has_album_keyword = any(keyword in title for keyword in album_keywords)
        
        # Check for specific album if provided
        if album:
            album_lower = album.lower()
            has_album_match = album_lower in title
            return has_album_keyword or has_album_match
        
        return has_album_keyword
    
    def _filter_quality_content(self, results: List[Result]) -> List[Result]:
        """Filter results for likely high-quality album content"""
        filtered = []
        
        for result in results:
            title_lower = result.title.lower()
            
            # Skip obvious low-quality content
            skip_keywords = [
                'reaction', 'review', 'cover', 'karaoke', 'instrumental',
                'remix', 'mashup', 'live', 'concert', 'acoustic'
            ]
            
            if any(keyword in title_lower for keyword in skip_keywords):
                continue
            
            # Prefer content with album indicators
            prefer_keywords = ['full album', 'complete', 'playlist', 'discography']
            if any(keyword in title_lower for keyword in prefer_keywords):
                result.quality_score = 1.0
            else:
                result.quality_score = 0.5
            
            filtered.append(result)
        
        return filtered
    
    def get_service_info(self) -> Dict[str, Any]:
        """Get service information and status"""
        return {
            "name": self.service_name,
            "description": "Direct YouTube Music search for albums and playlists",
            "cookie_support": True,
            "search_strategies": ["ytmusicsearch", "ytsearch with music keywords"],
            "status": "ready"
        }