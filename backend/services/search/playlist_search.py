#!/usr/bin/env python3
"""
YouTube Music Downloader - Playlist Search Service  
Searches for album playlists created by users, labels, and official channels
"""

import asyncio
import subprocess
import json
import re
from typing import List, Optional, Dict, Any
from datetime import datetime

from backend.core.models import Result
from backend.services.youtube_deduplicator import YouTubeDeduplicator


class PlaylistSearchService:
    """
    Stateless search service for finding album playlists on YouTube
    Focuses on user-created and official album playlists
    """
    
    def __init__(self):
        self.service_name = "playlist_search"    
    async def search(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None) -> List[Result]:
        """
        Search for album playlists on YouTube
        
        Args:
            artist: Artist name to search for
            album: Optional specific album name
            cookie_options: Cookie options from StateManager (single source of truth)
            
        Returns:
            List of Result objects (stateless - doesn't modify state)
        """
        print(f"PlaylistSearchService: Searching for playlists '{artist}'" + 
              (f" album '{album}'" if album else ""))
        
        # Use empty dict if no cookie options provided
        if cookie_options is None:
            cookie_options = {}
        
        
        results = []
        
        try:
            # Strategy 1: Search for official playlists
            official_results = await self._search_official_playlists(artist, album, cookie_options, ytdlp_executor)
            results.extend(official_results)
            
            # Strategy 2: Search for user-created album playlists
            user_results = await self._search_user_album_playlists(artist, album, cookie_options, ytdlp_executor)
            results.extend(user_results)
            
            # Strategy 3: Search for label/compilation playlists
            label_results = await self._search_label_playlists(artist, album, cookie_options, ytdlp_executor)
            results.extend(label_results)
            
            print(f"PlaylistSearchService: Found {len(results)} total playlist results")
            
        except Exception as e:
            print(f"PlaylistSearchService: Search failed: {e}")
        
        return results
    
    async def _search_official_playlists(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None) -> List[Result]:
        """Search for official artist/label playlists"""
        results = []
        
        try:
            # Build queries for official playlists
            queries = []
            
            if album:
                queries = [
                    f"{artist} {album} playlist",
                    f"{artist} {album} official playlist",
                    f'"{artist}" "{album}" playlist'
                ]
            else:
                queries = [
                    f"{artist} album playlist",
                    f"{artist} discography playlist",
                    f"{artist} greatest hits playlist"
                ]
            
            for query in queries:
                playlist_results = await self._search_playlists_with_query(query, cookie_options, ytdlp_executor)
                # Filter for official content
                official_filtered = [r for r in playlist_results if self._is_likely_official(r)]
                results.extend(official_filtered)
            
        except Exception as e:
            print(f"PlaylistSearchService: Official playlist search failed: {e}")
        
        return results
    
    async def _search_user_album_playlists(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None) -> List[Result]:
        """Search for user-created album playlists"""
        results = []
        
        try:
            # Build queries for user playlists
            queries = []
            
            if album:
                queries = [
                    f"{artist} {album} full album playlist",
                    f"{artist} {album} complete album"
                ]
            else:
                queries = [
                    f"{artist} full album playlist",
                    f"{artist} complete album playlist"
                ]
            
            for query in queries:
                playlist_results = await self._search_playlists_with_query(query, cookie_options, ytdlp_executor)
                # Filter for album-like content
                album_filtered = [r for r in playlist_results if self._is_likely_album_playlist(r, artist, album)]
                results.extend(album_filtered)
            
        except Exception as e:
            print(f"PlaylistSearchService: User playlist search failed: {e}")
        
        return results
    
    async def _search_label_playlists(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None) -> List[Result]:
        """Search for record label and compilation playlists"""
        results = []
        
        try:
            # Build queries for label content
            label_keywords = ["Records", "Music", "Entertainment", "Label"]
            
            queries = []
            if album:
                for keyword in label_keywords:
                    queries.append(f"{artist} {album} {keyword}")
            else:
                for keyword in label_keywords:
                    queries.append(f"{artist} album {keyword}")
            
            for query in queries:
                playlist_results = await self._search_playlists_with_query(query, cookie_options, ytdlp_executor)
                # Filter for label content
                label_filtered = [r for r in playlist_results if self._is_likely_label_content(r)]
                results.extend(label_filtered)
            
        except Exception as e:
            print(f"PlaylistSearchService: Label playlist search failed: {e}")
        
        return results
    
    async def _search_playlists_with_query(self, query: str, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None) -> List[Result]:
        """Execute a single playlist search query"""
        results = []
        
        try:
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json", 
                "--default-search", "ytsearch10:",  # Limit to 10 results per query
                f"{query} playlist"
            ]
            
            # Add cookie options
            if "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            # Always use ytdlp_executor for optimal performance
            if not ytdlp_executor:
                print("PlaylistSearchService: Warning - no ytdlp_executor provided, skipping query")
                return results
                
            stdout, stderr, returncode = await ytdlp_executor(cmd)
            stdout = stdout.encode() if isinstance(stdout, str) else stdout
            
            if returncode == 0 and stdout:
                lines = stdout.decode('utf-8').strip().split('\n')
                
                for line in lines:
                    try:
                        data = json.loads(line)
                        
                        # Only process playlist-like content
                        if not self._is_playlist_content(data):
                            continue
                        
                        # Extract artist from query
                        artist = query.split()[0]
                        
                        # Extract thumbnail URL (prefer best quality)
                        thumbnail_url = None
                        if 'thumbnail' in data:
                            thumbnail_url = data['thumbnail']
                        elif 'thumbnails' in data and data['thumbnails']:
                            thumbnail_url = data['thumbnails'][-1].get('url')  # Last is usually best quality

                        # Extract track count (playlist_count for playlists, 1 for single videos >10min)
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
            print(f"PlaylistSearchService: Query '{query}' failed: {e}")
        
        return results
    
    def _is_playlist_content(self, data: Dict[str, Any]) -> bool:
        """Check if content is a playlist"""
        title = data.get('title', '').lower()
        
        # Look for playlist indicators
        playlist_indicators = [
            'playlist', 'album', 'full album', 'complete', 
            'collection', 'compilation', 'discography'
        ]
        
        return any(indicator in title for indicator in playlist_indicators)
    
    def _is_likely_official(self, result: Result) -> bool:
        """Check if playlist is likely official"""
        channel_lower = result.channel.lower()
        title_lower = result.title.lower()
        
        # Official channel indicators
        official_indicators = [
            'vevo', 'records', 'music', 'official',
            'entertainment', 'label'
        ]
        
        # Check channel name
        for indicator in official_indicators:
            if indicator in channel_lower:
                return True
        
        # Check if title mentions official
        return 'official' in title_lower
    
    def _is_likely_album_playlist(self, result: Result, artist: str, album: Optional[str] = None) -> bool:
        """Check if playlist represents a complete album"""
        title_lower = result.title.lower()
        artist_lower = artist.lower()
        
        # Must contain artist name
        if artist_lower not in title_lower:
            return False
        
        # Check for album indicators
        album_indicators = ['full album', 'complete album', 'album', 'full']
        has_album_indicator = any(indicator in title_lower for indicator in album_indicators)
        
        # If specific album provided, check for match
        if album:
            album_lower = album.lower()
            has_album_match = album_lower in title_lower
            return has_album_indicator or has_album_match
        
        return has_album_indicator
    
    def _is_likely_label_content(self, result: Result) -> bool:
        """Check if content is from a record label"""
        channel_lower = result.channel.lower()
        
        # Label indicators
        label_indicators = [
            'records', 'label', 'entertainment', 'music',
            'distribution', 'publishing'
        ]
        
        return any(indicator in channel_lower for indicator in label_indicators)
    
    def get_service_info(self) -> Dict[str, Any]:
        """Get service information and status"""
        return {
            "name": self.service_name,
            "description": "Searches for album playlists from official channels, users, and labels",
            "cookie_support": True,
            "search_strategies": ["official playlists", "user album playlists", "label playlists"],
            "status": "ready"
        }