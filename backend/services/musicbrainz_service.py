#!/usr/bin/env python3
"""
YouTube Music Downloader - MusicBrainz Metadata Service
Retrieves album metadata from MusicBrainz database for normalized titles
"""

import asyncio
import aiohttp
import json
from typing import Dict, Optional, List, Tuple, Any
from datetime import datetime
from urllib.parse import quote


class MusicBrainzService:
    """
    MusicBrainz metadata retrieval service
    Part of extensible metadata architecture supporting multiple sources
    """
    
    def __init__(self):
        self.service_name = "musicbrainz"
        self.session = None
        self.base_url = "https://musicbrainz.org/ws/2"
        self.rate_limit_delay = 1.0  # MusicBrainz requires 1 request per second
        self.last_request_time = 0  # Will be set on first request
        
    async def _get_session(self):
        """Get or create aiohttp session with proper headers"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=10, connect=5)
            headers = {
                'User-Agent': 'YT-Music-Downloader/1.0 (https://github.com/user/yt-music-downloader)',
                'Accept': 'application/json'
            }
            self.session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        return self.session
    
    async def _rate_limit(self):
        """Enforce MusicBrainz rate limiting (1 request per second)"""
        import time
        now = time.time()
        
        # Skip rate limiting on first request
        if self.last_request_time == 0:
            self.last_request_time = now
            return
            
        time_since_last = now - self.last_request_time
        
        if time_since_last < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - time_since_last
            await asyncio.sleep(sleep_time)
        
        # Update timestamp to mark this request time
        self.last_request_time = time.time()
    
    async def search_release(self, artist: str, album: str) -> Optional[Dict[str, Any]]:
        """
        Search MusicBrainz for a release (album)
        
        Args:
            artist: Normalized artist name
            album: Normalized album title
            
        Returns:
            MusicBrainz metadata dict or None if not found
        """
        try:
            await self._rate_limit()
            session = await self._get_session()
            
            # Construct MusicBrainz query
            # Use exact artist and album matching for best results
            query = f'artist:"{artist}" AND release:"{album}"'
            encoded_query = quote(query)
            
            url = f"{self.base_url}/release/"
            params = {
                'query': query,
                'fmt': 'json',
                'limit': 5,  # Get top 5 matches for quality comparison
                'inc': 'artist-credits+release-groups+recordings'  # Include detailed info
            }
            
            print(f"MusicBrainzService: Searching for artist:'{artist}' release:'{album}'")
            
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    releases = data.get('releases', [])
                    
                    if releases:
                        # Find best match based on score and metadata quality
                        best_release = self._select_best_release(releases, artist, album)
                        if best_release:
                            return await self._enrich_release_metadata(best_release)
                    
                    print(f"MusicBrainzService: No releases found for '{artist} - {album}'")
                    return None
                    
                elif response.status == 429:
                    print("MusicBrainzService: Rate limited, waiting...")
                    await asyncio.sleep(2)
                    return await self.search_release(artist, album)  # Retry once
                else:
                    print(f"MusicBrainzService: HTTP {response.status} for '{artist} - {album}'")
                    return None
                    
        except asyncio.TimeoutError:
            print(f"MusicBrainzService: Timeout searching for '{artist} - {album}'")
            return None
        except Exception as e:
            print(f"MusicBrainzService: Error searching for '{artist} - {album}': {e}")
            return None
    
    def _select_best_release(self, releases: List[Dict], target_artist: str, target_album: str) -> Optional[Dict]:
        """
        Select the best matching release from MusicBrainz results
        
        Args:
            releases: List of MusicBrainz release objects
            target_artist: Target artist name
            target_album: Target album title
            
        Returns:
            Best matching release or None
        """
        best_release = None
        best_score = 0
        
        for release in releases:
            score = 0
            
            # Check artist match
            if 'artist-credit' in release:
                for credit in release['artist-credit']:
                    if isinstance(credit, dict) and 'artist' in credit:
                        mb_artist = credit['artist']['name'].lower()
                        if target_artist.lower() in mb_artist or mb_artist in target_artist.lower():
                            score += 10
                            break
            
            # Check album title match
            mb_title = release.get('title', '').lower()
            target_lower = target_album.lower()
            if mb_title == target_lower:
                score += 20  # Exact match
            elif target_lower in mb_title or mb_title in target_lower:
                score += 10  # Partial match
            
            # Prefer releases with more complete metadata
            if release.get('release-group'):
                score += 5
            if release.get('date'):
                score += 3
            if release.get('country'):
                score += 2
                
            # Use MusicBrainz's own score if available
            if 'score' in release:
                score += release['score'] / 10  # Scale down MB score
            
            if score > best_score:
                best_score = score
                best_release = release
        
        print(f"MusicBrainzService: Best match score {best_score} for '{target_artist} - {target_album}'")
        return best_release if best_score >= 10 else None  # Minimum threshold
    
    async def _enrich_release_metadata(self, release: Dict) -> Dict[str, Any]:
        """
        Enrich release metadata with additional MusicBrainz data
        
        Args:
            release: Basic MusicBrainz release object
            
        Returns:
            Enriched metadata dictionary
        """
        metadata = {
            'service': 'musicbrainz',
            'retrieved_at': datetime.utcnow().isoformat(),
            'mbid': release['id'],
            'mb_title': release.get('title'),
            'mb_artist': None,
            'mb_date': release.get('date'),
            'mb_country': release.get('country'),
            'mb_status': release.get('status'),
            'mb_packaging': release.get('packaging'),
            'mb_track_count': None,
            'mb_release_group_id': None,
            'mb_release_group_type': None,
            'mb_quality': 'high' if release.get('date') and release.get('country') else 'medium'
        }
        
        # Extract artist information
        if 'artist-credit' in release and release['artist-credit']:
            artists = []
            for credit in release['artist-credit']:
                if isinstance(credit, dict) and 'artist' in credit:
                    artists.append(credit['artist']['name'])
            if artists:
                metadata['mb_artist'] = ' & '.join(artists)
        
        # Extract release group information
        if 'release-group' in release:
            rg = release['release-group']
            metadata['mb_release_group_id'] = rg.get('id')
            metadata['mb_release_group_type'] = rg.get('primary-type')
        
        # Get track count if available
        if 'media' in release:
            total_tracks = sum(medium.get('track-count', 0) for medium in release['media'])
            metadata['mb_track_count'] = total_tracks if total_tracks > 0 else None
        
        print(f"MusicBrainzService: Enriched metadata for MBID {metadata['mbid']}")
        return metadata
    
    async def close(self):
        """Close the aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    def get_service_info(self) -> Dict[str, Any]:
        """Get service information"""
        return {
            'name': self.service_name,
            'description': 'MusicBrainz metadata retrieval',
            'base_url': self.base_url,
            'rate_limit': f'{self.rate_limit_delay}s per request',
            'status': 'ready'
        }


class MetadataCache:
    """
    Cache for metadata queries to prevent duplicate API calls
    Key format: "{service}:{artist}:{album}"
    """
    
    def __init__(self, cache_duration_hours: int = 24):
        self.cache: Dict[str, Tuple[Optional[Dict], datetime]] = {}
        self.cache_duration_hours = cache_duration_hours
        self.hits = 0
        self.misses = 0
    
    def _make_key(self, service: str, artist: str, album: str) -> str:
        """Create cache key from service and normalized metadata"""
        return f"{service}:{artist.lower()}:{album.lower()}"
    
    def get(self, service: str, artist: str, album: str) -> Optional[Dict]:
        """
        Get cached metadata for normalized artist/album
        
        Returns:
            Cached metadata dict or None if not cached/expired
        """
        key = self._make_key(service, artist, album)
        
        if key in self.cache:
            metadata, cached_at = self.cache[key]
            
            # Check if cache is still valid
            age_hours = (datetime.utcnow() - cached_at).total_seconds() / 3600
            if age_hours < self.cache_duration_hours:
                self.hits += 1
                print(f"MetadataCache: Cache hit for {key}")
                return metadata
            else:
                # Expired, remove from cache
                del self.cache[key]
        
        self.misses += 1
        print(f"MetadataCache: Cache miss for {key}")
        return None
    
    def set(self, service: str, artist: str, album: str, metadata: Optional[Dict]):
        """Cache metadata result (including None for "not found")"""
        key = self._make_key(service, artist, album)
        self.cache[key] = (metadata, datetime.utcnow())
        print(f"MetadataCache: Cached result for {key}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            'size': len(self.cache),
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': f"{hit_rate:.1f}%",
            'cache_duration_hours': self.cache_duration_hours
        }
    
    def clear_expired(self):
        """Remove expired entries from cache"""
        now = datetime.utcnow()
        expired_keys = []
        
        for key, (metadata, cached_at) in self.cache.items():
            age_hours = (now - cached_at).total_seconds() / 3600
            if age_hours >= self.cache_duration_hours:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.cache[key]
        
        if expired_keys:
            print(f"MetadataCache: Cleaned {len(expired_keys)} expired entries")