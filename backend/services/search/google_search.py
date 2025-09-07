#!/usr/bin/env python3
"""
YouTube Music Downloader - Google Search Service
Uses Google search to find YouTube links for albums (fallback strategy)
"""

import asyncio
import subprocess
import json
import re
from typing import List, Optional, Dict, Any
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from backend.core.models import Result
from backend.services.youtube_deduplicator import YouTubeDeduplicator


class GoogleSearchService:
    """
    Stateless search service using Google to find YouTube album links
    This is a fallback strategy when direct YouTube searches fail
    """
    
    def __init__(self):
        self.service_name = "google_search"    
    async def search(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """
        Use Google search to find YouTube album links
        
        Args:
            artist: Artist name to search for
            album: Optional specific album name
            cookie_options: Cookie options from StateManager (single source of truth)
            
        Returns:
            List of Result objects (stateless - doesn't modify state)
        """
        print(f"GoogleSearchService: Searching for '{artist}'" + 
              (f" album '{album}'" if album else ""))
        
        # Use empty dict if no cookie options provided
        if cookie_options is None:
            cookie_options = {}
        
        
        results = []
        
        try:
            # Strategy 1: Google search for YouTube album links
            google_results = await self._google_search_youtube_albums(artist, album, cookie_options)
            results.extend(google_results)
            
            # Strategy 2: Site-specific search on YouTube
            site_results = await self._site_specific_youtube_search(artist, album, cookie_options)
            results.extend(site_results)
            
            print(f"GoogleSearchService: Found {len(results)} total results")
            
        except Exception as e:
            print(f"GoogleSearchService: Search failed: {e}")
        
        return results
    
    async def _google_search_youtube_albums(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Use Google search with site:youtube.com restriction"""
        results = []
        
        try:
            # Build Google search queries
            queries = []
            
            if album:
                queries = [
                    f'site:youtube.com "{artist}" "{album}" full album',
                    f'site:youtube.com "{artist}" "{album}" playlist',
                    f'site:youtube.com {artist} {album} complete'
                ]
            else:
                queries = [
                    f'site:youtube.com "{artist}" full album',
                    f'site:youtube.com "{artist}" discography',
                    f'site:youtube.com {artist} album collection'
                ]
            
            for query in queries:
                query_results = await self._execute_google_search(query, artist, cookie_options)
                results.extend(query_results)
            
        except Exception as e:
            print(f"GoogleSearchService: Google YouTube search failed: {e}")
        
        return results
    
    async def _site_specific_youtube_search(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Search specific YouTube URL patterns"""
        results = []
        
        try:
            # Use yt-dlp to search with Google-like queries
            queries = []
            
            if album:
                queries = [
                    f'{artist} {album} site:youtube.com',
                    f'"{artist} {album}" youtube full album'
                ]
            else:
                queries = [
                    f'{artist} album site:youtube.com',
                    f'"{artist}" youtube discography'
                ]
            
            for query in queries:
                query_results = await self._search_with_yt_dlp(query, artist, cookie_options)
                results.extend(query_results)
            
        except Exception as e:
            print(f"GoogleSearchService: Site-specific search failed: {e}")
        
        return results
    
    async def _execute_google_search(self, query: str, artist: str, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """
        Execute Google search and extract YouTube URLs
        Note: This is a simplified implementation. Real Google search would require API access.
        """
        results = []
        
        try:
            # Use yt-dlp with a search query that mimics Google results
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--default-search", "ytsearch8:",  # Limited results for Google-style search
                query.replace('site:youtube.com', '').strip()  # Remove site restriction for yt-dlp
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
            
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
            
            if process.returncode == 0 and stdout:
                lines = stdout.decode('utf-8').strip().split('\n')
                
                for line in lines:
                    try:
                        data = json.loads(line)
                        
                        # Filter for album-like content based on Google search intent
                        if self._matches_google_search_intent(data, query):
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
                                # Mark as potentially lower quality (Google fallback)
                                result.quality_score = 0.6
                                results.append(result)
                                
                    except json.JSONDecodeError:
                        continue
        
        except Exception as e:
            print(f"GoogleSearchService: Query '{query}' failed: {e}")
        
        return results
    
    async def _search_with_yt_dlp(self, query: str, artist: str, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Use yt-dlp for site-specific searches"""
        results = []
        
        try:
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--default-search", "ytsearch5:",  # Small result set
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
            
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
            
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
                            # Lower quality score for fallback method
                            result.quality_score = 0.4
                            results.append(result)
                            
                    except json.JSONDecodeError:
                        continue
        
        except Exception as e:
            print(f"GoogleSearchService: yt-dlp search '{query}' failed: {e}")
        
        return results
    
    def _matches_google_search_intent(self, data: Dict[str, Any], original_query: str) -> bool:
        """Check if result matches the original Google search intent"""
        title = data.get('title', '').lower()
        
        # Extract key terms from original query
        query_lower = original_query.lower()
        
        # Check for album indicators (what Google search was looking for)
        album_terms = ['full album', 'album', 'playlist', 'complete', 'discography']
        has_album_term = any(term in title for term in album_terms)
        
        # Check for artist mention in title
        # Extract artist from query (remove site: restrictions and quotes)
        clean_query = re.sub(r'site:\S+', '', query_lower)
        clean_query = clean_query.replace('"', '').strip()
        
        # Try to find artist name in the clean query
        query_words = clean_query.split()
        if len(query_words) >= 1:
            potential_artist = query_words[0]
            has_artist = potential_artist in title
        else:
            has_artist = True  # Default to true if can't extract artist
        
        return has_album_term and has_artist
    
    def _is_valid_youtube_url(self, url: str) -> bool:
        """Validate that URL is a proper YouTube URL"""
        if not url:
            return False
        
        try:
            parsed = urlparse(url)
            return (parsed.netloc in ['www.youtube.com', 'youtube.com', 'youtu.be'] and
                    ('watch' in parsed.path or 'playlist' in parsed.path or parsed.netloc == 'youtu.be'))
        except:
            return False
    
    def get_service_info(self) -> Dict[str, Any]:
        """Get service information and status"""
        return {
            "name": self.service_name,
            "description": "Google search fallback for finding YouTube album links",
            "cookie_support": True,
            "search_strategies": ["google site:youtube.com", "yt-dlp with google-style queries"],
            "quality_score": 0.5,  # Lower priority fallback method
            "status": "ready"
        }