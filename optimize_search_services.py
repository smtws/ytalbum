#!/usr/bin/env python3
"""
Optimize search services to reduce duplicates at the source
"""

import sys
import os
sys.path.insert(0, '/home/tordt/YT-Downloads')

from typing import List, Dict, Any, Optional, Set
from backend.core.models import Result

def deduplicate_results_internally(results: List[Result]) -> List[Result]:
    """
    Internal deduplication helper for search services
    Removes duplicates based on youtube_id
    """
    seen_ids: Set[str] = set()
    unique_results: List[Result] = []
    
    for result in results:
        if result.youtube_id and result.youtube_id not in seen_ids:
            seen_ids.add(result.youtube_id)
            unique_results.append(result)
        elif not result.youtube_id:
            # Keep results without IDs (shouldn't happen but safety check)
            unique_results.append(result)
    
    return unique_results


def optimize_genre_context_search():
    """
    Optimizations for genre_context_search to reduce duplicates:
    
    1. Reduce overlapping genre searches
    2. Use more specific search queries  
    3. Add internal deduplication
    4. Smarter genre detection
    """
    
    optimizations = """
    # Genre Context Search Optimizations
    
    ## Problem: 62.6% duplicate rate (92 out of 147 results)
    
    ## Root Causes:
    - Searching multiple genres (7+) returns same popular videos
    - Generic queries like "Sabaton metal album" and "Sabaton rock album" return similar results
    - No deduplication between strategy results
    
    ## Solutions:
    
    ### 1. Reduce Genre Overlap
    Instead of searching 5 priority genres + 2 detected genres:
    - Auto-detect genre first
    - Only search detected genre + 1-2 complementary genres
    - For Sabaton: detect "metal", only add "power metal" or "heavy metal" (not rock, pop, etc.)
    
    ### 2. More Specific Queries
    Current: f"{artist} {genre} album"
    Better: f"{artist} {genre} full album -playlist -compilation"
    
    ### 3. Add Internal Deduplication
    - Track youtube_ids within the service
    - Before extending results, check for duplicates
    
    ### 4. Limit Results Per Strategy
    - Currently: limit=8 per genre query
    - Better: limit=3-4 per genre, focus on quality over quantity
    """
    
    return optimizations


def optimize_channel_search():
    """
    Optimizations for channel_search to reduce duplicates:
    
    1. Avoid extracting same playlists multiple times
    2. Deduplicate playlist items
    3. Limit playlist extraction depth
    """
    
    optimizations = """
    # Channel Search Optimizations
    
    ## Problem: 34.8% duplicate rate (202 out of 580 results)
    
    ## Root Causes:
    - Extracting multiple playlists from same channel that contain overlapping videos
    - Same video appears in multiple playlists (e.g., "Best of" and "Full Album")
    
    ## Solutions:
    
    ### 1. Track Extracted Playlists
    - Keep set of playlist IDs already processed
    - Skip if playlist already extracted
    
    ### 2. Internal Deduplication
    - Track youtube_ids as videos are discovered
    - Skip adding if already seen
    
    ### 3. Prioritize Album Playlists
    - Look for keywords: "full album", "album", "[year]"
    - Skip "mix", "top", "best of" playlists
    
    ### 4. Limit Playlist Depth
    - Currently extracts ALL videos from channel
    - Better: Extract top N playlists only (sorted by relevance)
    """
    
    return optimizations


def generate_optimized_genre_search():
    """Generate optimized genre_context_search.py code"""
    
    code = '''
    async def search(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Optimized search with genre context and deduplication"""
        print(f"GenreContextSearchService: Searching for '{artist}'" + 
              (f" album '{album}'" if album else ""))
        
        if cookie_options is None:
            cookie_options = {}
        
        all_results = []
        seen_youtube_ids = set()  # Track IDs to avoid duplicates
        
        try:
            # Strategy 1: Auto-detect genre and search ONLY detected genres
            detected_genres = await self._detect_artist_genre(artist, cookie_options)
            
            # Only use top detected genre + 1 complementary
            for genre in detected_genres[:2]:  # Reduced from multiple genres
                genre_results = await self._search_with_specific_genre(
                    artist, album, genre, cookie_options, limit=4  # Reduced from 8
                )
                
                # Deduplicate before adding
                for result in genre_results:
                    if result.youtube_id not in seen_youtube_ids:
                        seen_youtube_ids.add(result.youtube_id)
                        all_results.append(result)
            
            # Strategy 2: ONE music term search (not multiple)
            if len(all_results) < 20:  # Only if we need more results
                music_term = "full album" if not album else "album"
                query = f"{artist} {album if album else ''} {music_term}"
                
                term_results = await self._execute_search(
                    query, artist, cookie_options, limit=5
                )
                
                for result in term_results:
                    if result.youtube_id not in seen_youtube_ids:
                        seen_youtube_ids.add(result.youtube_id)
                        all_results.append(result)
            
            print(f"GenreContextSearchService: Found {len(all_results)} unique results")
            
        except Exception as e:
            print(f"GenreContextSearchService: Search failed: {e}")
        
        return all_results
    '''
    
    return code


def generate_optimized_channel_search():
    """Generate optimized channel_search.py code"""
    
    code = '''
    async def _extract_channel_content(self, channel_url: str, artist: str, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Optimized channel content extraction with deduplication"""
        results = []
        seen_youtube_ids = set()  # Track to avoid duplicates
        extracted_playlists = set()  # Track processed playlists
        
        try:
            # First, get channel playlists (not all videos)
            cmd = [
                "yt-dlp",
                "--flat-playlist", 
                "--dump-json",
                "--playlist-items", "1-100",  # Limit items extracted
                f"{channel_url}/playlists"  # Get playlists, not videos
            ]
            
            if "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            # Process playlists
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            
            if stdout:
                lines = stdout.decode('utf-8').strip().split('\\n')
                
                # Filter for album playlists
                album_playlists = []
                for line in lines:
                    try:
                        data = json.loads(line)
                        title = data.get('title', '').lower()
                        
                        # Prioritize album playlists
                        if any(keyword in title for keyword in ['album', 'full', 'complete']):
                            if 'mix' not in title and 'best' not in title:
                                album_playlists.append(data.get('url'))
                    except:
                        continue
                
                # Extract from top album playlists only
                for playlist_url in album_playlists[:5]:  # Limit to 5 best playlists
                    if playlist_url in extracted_playlists:
                        continue
                    
                    extracted_playlists.add(playlist_url)
                    playlist_results = await self._extract_playlist_tracks(
                        playlist_url, artist, cookie_options
                    )
                    
                    # Deduplicate
                    for result in playlist_results:
                        if result.youtube_id not in seen_youtube_ids:
                            seen_youtube_ids.add(result.youtube_id)
                            results.append(result)
            
        except Exception as e:
            print(f"ChannelSearchService: Optimized extraction failed: {e}")
        
        return results
    '''
    
    return code


if __name__ == "__main__":
    print("=" * 80)
    print("SEARCH SERVICE OPTIMIZATION ANALYSIS")
    print("=" * 80)
    
    print(optimize_genre_context_search())
    print(optimize_channel_search())
    
    print("\n" + "=" * 80)
    print("IMPLEMENTATION STRATEGY")
    print("=" * 80)
    
    print("""
    ## Implementation Plan:
    
    1. Add internal deduplication to both services
    2. Reduce search breadth in genre_context_search:
       - From 7+ genre searches to 2-3 max
       - Smaller result limits per query
    3. Optimize channel_search extraction:
       - Focus on album playlists only
       - Skip compilation/mix playlists
       - Limit extraction depth
    4. Better query specificity:
       - Add negative keywords (-playlist -mix -compilation)
       - Use more targeted search terms
    
    ## Expected Results:
    - genre_context_search: Reduce from 147 to ~50-60 unique results
    - channel_search: Reduce from 580 to ~300-400 unique results
    - Overall duplicate rate: From 35-63% to under 10%
    """)