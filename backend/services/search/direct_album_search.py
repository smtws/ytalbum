#!/usr/bin/env python3
"""
YouTube Music Downloader - Direct Album Search Service
Direct artist + album name searches with exact matching
"""

import asyncio
import subprocess
import json
import re
from typing import List, Optional, Dict, Any
from datetime import datetime

from backend.core.models import Result
from backend.services.youtube_deduplicator import YouTubeDeduplicator


class DirectAlbumSearchService:
    """
    Stateless search service for direct artist + album searches
    Uses exact album names and focuses on precision over coverage
    """
    
    def __init__(self):
        self.service_name = "direct_album_search"    
    async def search(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """
        Search for specific artist + album combinations
        
        Args:
            artist: Artist name to search for
            album: Optional specific album name
            cookie_options: Cookie options from StateManager (single source of truth)
            
        Returns:
            List of Result objects (stateless - doesn't modify state)
        """
        print(f"DirectAlbumSearchService: Searching for '{artist}'" + 
              (f" album '{album}'" if album else " (no specific album)"))
        
        # Use empty dict if no cookie options provided
        if cookie_options is None:
            cookie_options = {}
        
        
        results = []
        
        try:
            if album:
                # Strategy 1: Exact artist + album search
                exact_results = await self._search_exact_album(artist, album, cookie_options)
                results.extend(exact_results)
                
                # Strategy 2: Quoted search for precision
                quoted_results = await self._search_quoted_album(artist, album, cookie_options)
                results.extend(quoted_results)
                
                # Strategy 3: Album year context (if detectable)
                year_results = await self._search_with_year_context(artist, album, cookie_options)
                results.extend(year_results)
            else:
                # General artist search when no specific album
                general_results = await self._search_artist_albums(artist, cookie_options)
                results.extend(general_results)
            
            print(f"DirectAlbumSearchService: Found {len(results)} total results")
            
        except Exception as e:
            print(f"DirectAlbumSearchService: Search failed: {e}")
        
        return results
    
    async def _search_exact_album(self, artist: str, album: str, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Search for exact artist + album combination"""
        results = []
        
        try:
            # Build exact search queries
            queries = [
                f"{artist} {album}",
                f"{artist} {album} full album",
                f"{artist} {album} complete album",
                f"{artist} - {album}",  # Common format with dash
                f"{artist}: {album}"   # Common format with colon
            ]
            
            for query in queries:
                query_results = await self._execute_search(query, artist, cookie_options)
                # Filter for high relevance
                relevant = [r for r in query_results if self._is_highly_relevant(r, artist, album)]
                results.extend(relevant)
            
        except Exception as e:
            print(f"DirectAlbumSearchService: Exact search failed: {e}")
        
        return results
    
    async def _search_quoted_album(self, artist: str, album: str, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Search with quoted terms for precision"""
        results = []
        
        try:
            # Quoted searches for precision
            queries = [
                f'"{artist}" "{album}"',
                f'"{artist}" "{album}" album',
                f'"{artist} - {album}"',
                f'"{artist}: {album}"'
            ]
            
            for query in queries:
                query_results = await self._execute_search(query, artist, cookie_options)
                results.extend(query_results)
            
        except Exception as e:
            print(f"DirectAlbumSearchService: Quoted search failed: {e}")
        
        return results
    
    async def _search_with_year_context(self, artist: str, album: str, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Search with potential year context"""
        results = []
        
        try:
            # Try common album release year patterns
            # This is a basic implementation - could be enhanced with actual release date data
            current_year = datetime.now().year
            potential_years = range(current_year - 20, current_year + 1)  # Last 20 years
            
            # Sample a few potential years for context
            test_years = [current_year, current_year - 1, current_year - 5, current_year - 10]
            
            for year in test_years:
                queries = [
                    f"{artist} {album} {year}",
                    f"{artist} {album} ({year})"
                ]
                
                for query in queries:
                    query_results = await self._execute_search(query, artist, cookie_options, limit=3)  # Small limit
                    # Only take highly relevant results for year searches
                    relevant = [r for r in query_results if self._is_highly_relevant(r, artist, album)]
                    results.extend(relevant)
            
        except Exception as e:
            print(f"DirectAlbumSearchService: Year context search failed: {e}")
        
        return results
    
    async def _search_artist_albums(self, artist: str, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Search for artist's albums when no specific album provided"""
        results = []
        
        try:
            # General album searches
            queries = [
                f"{artist} album",
                f"{artist} full album",
                f"{artist} discography",
                f'"{artist}" album'
            ]
            
            for query in queries:
                query_results = await self._execute_search(query, artist, cookie_options)
                results.extend(query_results)
            
        except Exception as e:
            print(f"DirectAlbumSearchService: Artist album search failed: {e}")
        
        return results
    
    async def _execute_search(self, query: str, artist: str, cookie_options: Optional[Dict[str, Any]] = None, limit: int = 12) -> List[Result]:
        """Execute a single search query"""
        results = []
        
        try:
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--default-search", f"ytsearch{limit}:",
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
            
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            
            if process.returncode == 0 and stdout:
                lines = stdout.decode('utf-8').strip().split('\n')
                
                for line in lines:
                    try:
                        data = json.loads(line)
                        
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
                            # High quality score for direct searches
                            result.quality_score = 0.9
                            results.append(result)
                            
                    except json.JSONDecodeError:
                        continue
        
        except Exception as e:
            print(f"DirectAlbumSearchService: Query '{query}' failed: {e}")
        
        return results
    
    def _is_highly_relevant(self, result: Result, artist: str, album: str) -> bool:
        """Check if result is highly relevant to the search"""
        title_lower = result.title.lower()
        artist_lower = artist.lower()
        album_lower = album.lower()
        
        # Must contain both artist and album
        has_artist = artist_lower in title_lower
        has_album = album_lower in title_lower
        
        if not (has_artist and has_album):
            return False
        
        # Bonus points for exact matches
        if f"{artist_lower} - {album_lower}" in title_lower:
            result.quality_score = 1.0
            return True
        
        if f"{artist_lower}: {album_lower}" in title_lower:
            result.quality_score = 1.0
            return True
        
        # Check for album indicators
        album_indicators = ['full album', 'complete album', 'album', 'full']
        has_album_indicator = any(indicator in title_lower for indicator in album_indicators)
        
        if has_album_indicator:
            result.quality_score = 0.95
            return True
        
        # Default to relevant if has both artist and album
        result.quality_score = 0.8
        return True
    
    def _extract_potential_year(self, title: str) -> Optional[int]:
        """Extract potential year from title"""
        # Look for 4-digit years in parentheses or standalone
        year_pattern = r'\b(19[89]\d|20[0-4]\d)\b'
        match = re.search(year_pattern, title)
        
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        
        return None
    
    def get_service_info(self) -> Dict[str, Any]:
        """Get service information and status"""
        return {
            "name": self.service_name,
            "description": "Direct artist + album searches with exact matching",
            "cookie_support": True,
            "search_strategies": ["exact matching", "quoted searches", "year context"],
            "quality_score": 0.9,  # High precision method
            "status": "ready"
        }