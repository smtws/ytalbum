#!/usr/bin/env python3
"""
YouTube Music Downloader - Genre Context Search Service
Searches with genre context to improve album discovery accuracy
"""

import asyncio
import subprocess
import json
import re
from typing import List, Optional, Dict, Any
from datetime import datetime

from backend.core.models import Result
from backend.services.youtube_deduplicator import YouTubeDeduplicator


class GenreContextSearchService:
    """
    Stateless search service that uses genre context to improve search accuracy
    Helps disambiguate artists with similar names across different genres
    """
    
    def __init__(self):
        self.service_name = "genre_context_search"        
        # Common genre categories for context
        self.genre_groups = {
            "metal": ["metal", "heavy metal", "death metal", "black metal", "thrash metal", "power metal"],
            "rock": ["rock", "hard rock", "punk rock", "alternative rock", "classic rock", "indie rock"],
            "pop": ["pop", "pop music", "mainstream", "chart", "radio"],
            "electronic": ["electronic", "edm", "techno", "house", "ambient", "synthwave"],
            "hip-hop": ["hip hop", "rap", "hip-hop", "urban", "trap"],
            "classical": ["classical", "orchestral", "symphony", "chamber music", "opera"],
            "country": ["country", "country music", "nashville", "bluegrass"],
            "jazz": ["jazz", "blues", "swing", "bebop", "smooth jazz"],
            "folk": ["folk", "acoustic", "singer-songwriter", "indie folk"],
            "reggae": ["reggae", "ska", "dub", "dancehall"]
        }
    
    async def search(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None) -> List[Result]:
        """
        Optimized search with genre context and internal deduplication
        
        Args:
            artist: Artist name to search for
            album: Optional specific album name
            cookie_options: Cookie options from StateManager (single source of truth)
            
        Returns:
            List of Result objects (stateless - doesn't modify state)
        """
        print(f"GenreContextSearchService: Searching for '{artist}'" + 
              (f" album '{album}'" if album else ""))
        
        # Use empty dict if no cookie options provided
        if cookie_options is None:
            cookie_options = {}
        
        results = []
        
        try:
            # Strategy 1: Auto-detect genre and search ONLY detected genres (reduced scope)
            detected_genres = await self._detect_artist_genre(artist, cookie_options, ytdlp_executor)
            
            # Only use top 2 detected genres (reduced from all detected + 5 priority genres)
            for genre in detected_genres[:2]:
                genre_results = await self._search_with_specific_genre(
                    artist, album, genre, cookie_options, ytdlp_executor, limit=4  # Reduced from 8
                )
                results.extend(genre_results)
            
            # Strategy 2: ONE focused search with best music term (not multiple strategies)
            if len(results) < 20:  # Only if we need more results
                music_term = "full album" if not album else "album"
                query = f"{artist} {album if album else ''} {music_term}"
                
                term_results = await self._execute_search(
                    query, artist, cookie_options, ytdlp_executor, limit=5
                )
                results.extend(term_results)
            
            print(f"GenreContextSearchService: Found {len(results)} results (optimized strategy)")
            
        except Exception as e:
            print(f"GenreContextSearchService: Search failed: {e}")
        
        return results
    
    async def _search_with_auto_genre(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None) -> List[Result]:
        """Auto-detect likely genre from initial search and use for context"""
        results = []
        
        try:
            # First, do a quick search to detect genre
            detected_genres = await self._detect_artist_genre(artist, cookie_options, ytdlp_executor)
            
            for genre in detected_genres[:2]:  # Use top 2 detected genres
                genre_results = await self._search_with_specific_genre(artist, album, genre, cookie_options, ytdlp_executor)
                results.extend(genre_results)
            
        except Exception as e:
            print(f"GenreContextSearchService: Auto-genre search failed: {e}")
        
        return results
    
    async def _search_multiple_genres(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None) -> List[Result]:
        """Search with multiple genre contexts"""
        results = []
        
        try:
            # Try popular genres that might help disambiguation
            priority_genres = ["metal", "rock", "pop", "electronic", "hip-hop"]
            
            for genre in priority_genres:
                genre_results = await self._search_with_specific_genre(artist, album, genre, cookie_options, ytdlp_executor, limit=5)
                results.extend(genre_results)
            
        except Exception as e:
            print(f"GenreContextSearchService: Multi-genre search failed: {e}")
        
        return results
    
    async def _search_with_music_terms(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None) -> List[Result]:
        """Search with general music terms for context"""
        results = []
        
        try:
            music_terms = ["music", "band", "musician", "song", "track"]
            
            for term in music_terms[:2]:  # Use first 2 terms
                if album:
                    queries = [
                        f"{artist} {album} {term}",
                        f"{artist} {album} album {term}"
                    ]
                else:
                    queries = [
                        f"{artist} album {term}",
                        f"{artist} {term} discography"
                    ]
                
                # Execute queries in parallel for better performance
                query_tasks = [
                    self._execute_search(query, artist, cookie_options, ytdlp_executor, limit=6)
                    for query in queries
                ]
                
                query_results_list = await asyncio.gather(*query_tasks, return_exceptions=True)
                
                for query_results in query_results_list:
                    if isinstance(query_results, Exception):
                        print(f"GenreContextSearchService: Query failed: {query_results}")
                    else:
                        results.extend(query_results)
            
        except Exception as e:
            print(f"GenreContextSearchService: Music terms search failed: {e}")
        
        return results
    
    async def _search_with_specific_genre(self, artist: str, album: Optional[str] = None, 
                                        genre: str = "", cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None, limit: int = 8) -> List[Result]:
        """Search with a specific genre context"""
        results = []
        
        try:
            # Get genre variations
            genre_terms = self.genre_groups.get(genre, [genre])
            primary_genre = genre_terms[0] if genre_terms else genre
            
            if album:
                queries = [
                    f'"{artist}" "{album}" {primary_genre} -mix -compilation',
                    f"{artist} {album} {primary_genre} full album"
                ]
            else:
                queries = [
                    f'"{artist}" {primary_genre} album -mix -playlist',
                    f"{artist} {primary_genre} discography -compilation"
                ]
            
            # Execute queries in parallel for better performance
            query_tasks = [
                self._execute_search(query, artist, cookie_options, ytdlp_executor, limit)
                for query in queries
            ]
            
            query_results_list = await asyncio.gather(*query_tasks, return_exceptions=True)
            
            for query_results in query_results_list:
                if isinstance(query_results, Exception):
                    print(f"GenreContextSearchService: Genre query failed: {query_results}")
                else:
                    # Filter for genre relevance
                    relevant = [r for r in query_results if self._is_genre_relevant(r, genre)]
                    results.extend(relevant)
            
        except Exception as e:
            print(f"GenreContextSearchService: Genre '{genre}' search failed: {e}")
        
        return results
    
    async def _detect_artist_genre(self, artist: str, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None) -> List[str]:
        """Detect artist's likely genre from a quick search"""
        detected_genres = []
        
        try:
            # Quick search to analyze titles/channels for genre indicators
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--default-search", "ytsearch5:",
                f"{artist} music"
            ]
            
            # Add cookie options
            if "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            # Always use ytdlp_executor for optimal performance
            if not ytdlp_executor:
                print("GenreContextSearchService: Warning - no ytdlp_executor provided, skipping genre detection")
                return ["rock", "pop"]  # Fallback genres
                
            stdout, stderr, returncode = await ytdlp_executor(cmd)
            stdout = stdout.encode() if isinstance(stdout, str) else stdout
            
            if returncode == 0 and stdout:
                lines = stdout.decode('utf-8').strip().split('\n')
                
                genre_mentions = {}
                
                for line in lines:
                    try:
                        data = json.loads(line)
                        title = data.get('title', '').lower()
                        channel = data.get('uploader', '').lower()
                        text = f"{title} {channel}"
                        
                        # Count genre mentions
                        for genre, terms in self.genre_groups.items():
                            for term in terms:
                                if term in text:
                                    genre_mentions[genre] = genre_mentions.get(genre, 0) + 1
                                    
                    except json.JSONDecodeError:
                        continue
                
                # Return genres sorted by mention frequency
                detected_genres = sorted(genre_mentions.keys(), 
                                       key=lambda g: genre_mentions[g], reverse=True)
            
        except Exception as e:
            print(f"GenreContextSearchService: Genre detection failed: {e}")
        
        return detected_genres or ["rock", "pop"]  # Default fallback genres
    
    async def _execute_search(self, query: str, artist: str, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None, limit: int = 8) -> List[Result]:
        """Execute a single search query"""
        results = []
        
        try:
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--default-search", f"ytsearch{limit}:",
                query
            ]
            
            # Add cookie options
            if "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            # Always use ytdlp_executor for optimal performance
            if not ytdlp_executor:
                print("GenreContextSearchService: Warning - no ytdlp_executor provided, skipping query")
                return results
                
            stdout, stderr, returncode = await ytdlp_executor(cmd)
            stdout = stdout.encode() if isinstance(stdout, str) else stdout
            
            if returncode == 0 and stdout:
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
                            # Genre context provides good quality
                            result.quality_score = 0.75
                            results.append(result)
                            
                    except json.JSONDecodeError:
                        continue
        
        except Exception as e:
            print(f"GenreContextSearchService: Query '{query}' failed: {e}")
        
        return results
    
    def _is_genre_relevant(self, result: Result, genre: str) -> bool:
        """Check if result is relevant to the genre context"""
        title_lower = result.title.lower()
        channel_lower = result.channel.lower()
        text = f"{title_lower} {channel_lower}"
        
        # Check for genre terms
        if genre in self.genre_groups:
            genre_terms = self.genre_groups[genre]
            has_genre_term = any(term in text for term in genre_terms)
            
            if has_genre_term:
                result.quality_score = 0.85  # Higher quality for genre match
                return True
        
        # Default to relevant (genre context still helpful)
        return True
    
    def get_service_info(self) -> Dict[str, Any]:
        """Get service information and status"""
        return {
            "name": self.service_name,
            "description": "Searches with genre context for better artist disambiguation",
            "cookie_support": True,
            "search_strategies": ["auto-genre detection", "multi-genre context", "music terms"],
            "supported_genres": list(self.genre_groups.keys()),
            "quality_score": 0.75,
            "status": "ready"
        }