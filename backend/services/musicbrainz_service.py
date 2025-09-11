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
    
    def __init__(self, config=None):
        self.service_name = "musicbrainz"
        self.session = None
        self.base_url = "https://musicbrainz.org/ws/2"
        self.rate_limit_delay = 1.0  # MusicBrainz requires 1 request per second
        self.last_request_time = 0  # Will be set on first request
        
        # Country prioritization configuration
        self.country_priority = {}
        if config and hasattr(config, 'release_country_priority'):
            self.country_priority = config.release_country_priority
        else:
            # Default prioritization if no config provided
            self.country_priority = {
                "US": 100, "DE": 90, "GB": 85, "XE": 80, "XW": 75,
                "CA": 70, "AU": 65, "JP": 60, "FR": 55, "NL": 50,
                "SE": 45, "NO": 40, "FI": 35
            }
        
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
    
    async def search_release(self, artist: str, album: str, track_count: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Search MusicBrainz for a release (album)
        
        Args:
            artist: Normalized artist name
            album: Normalized album title
            track_count: Expected track count from YouTube result (for better matching)
            
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
                        # Find best match based on score, metadata quality, and track count
                        best_release = self._select_best_release(releases, artist, album, track_count)
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
    
    def _select_best_release(self, releases: List[Dict], target_artist: str, target_album: str, target_track_count: Optional[int] = None) -> Optional[Dict]:
        """
        Select the best matching release from MusicBrainz results
        
        Args:
            releases: List of MusicBrainz release objects
            target_artist: Target artist name
            target_album: Target album title
            target_track_count: Expected track count from YouTube (for prioritization)
            
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
            
            # Country prioritization (significant impact on release selection)
            country = release.get('country', '')
            if country in self.country_priority:
                country_bonus = self.country_priority[country] / 10  # Scale to reasonable range
                score += country_bonus
                print(f"Country bonus: {country} (+{country_bonus})")
            elif country:
                score += 1  # Small bonus for having any country vs unknown
            
            # Note: Cover art evaluation removed since we fetch via separate API
                
            # Track count matching (highest priority for exact matches)
            if target_track_count and 'media' in release:
                release_track_count = sum(medium.get('track-count', 0) for medium in release['media'])
                if release_track_count > 0:
                    if release_track_count == target_track_count:
                        score += 25  # High bonus for exact track count match
                    elif abs(release_track_count - target_track_count) == 1:
                        score += 15  # Good bonus for close match (±1 track)
                    elif abs(release_track_count - target_track_count) <= 2:
                        score += 8   # Some bonus for reasonable match (±2 tracks)
                    else:
                        # Penalty for significant track count mismatch
                        track_diff = abs(release_track_count - target_track_count)
                        score -= min(track_diff * 2, 10)  # Penalty up to -10
            
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
        Enrich release metadata with raw MusicBrainz data plus essential annotations
        
        Args:
            release: Basic MusicBrainz release object
            
        Returns:
            Raw MusicBrainz data with service annotations and normalized frontend interface
        """
        # Start with complete raw MusicBrainz data
        metadata = release.copy()
        
        # Add essential service annotations for deduplication and tracking
        metadata.update({
            'service': 'musicbrainz',
            'retrieved_at': datetime.utcnow().isoformat(),
            'id': release['id']  # Critical: Keep id field for deduplication counting
        })
        
        # Try to fetch cover art from Cover Art Archive API (separate from MusicBrainz web service)
        release_id = release['id']
        cover_art_urls = await self._get_cover_art_urls(release_id)
        if cover_art_urls:
            metadata['cover_art_urls'] = cover_art_urls
            print(f"MusicBrainzService: Added cover art URLs for ID {metadata['id']}")
        else:
            print(f"MusicBrainzService: No cover art found for ID {metadata['id']}")
        
        # Create normalized frontend interface within metadata object 
        # This is INSIDE the metadata object, separate from result.normalized
        normalized = self._create_normalized_interface(release, cover_art_urls)
        metadata['normalized'] = normalized
        
        print(f"MusicBrainzService: Preserved raw metadata and normalized interface for ID {metadata['id']}")
        return metadata
    
    def _create_normalized_interface(self, release: Dict, cover_art_urls: Optional[Dict[str, str]]) -> Dict[str, Any]:
        """
        Create normalized frontend interface from MusicBrainz data
        Data is copied/duplicated here for unified frontend access
        
        Args:
            release: Raw MusicBrainz release object
            cover_art_urls: Cover art URLs if available
            
        Returns:
            Normalized metadata dict with standardized keys for frontend consumption
        """
        # Extract artist name from artist-credit structure
        artist_name = ""
        if 'artist-credit' in release:
            for credit in release['artist-credit']:
                if isinstance(credit, dict) and 'artist' in credit:
                    artist_name = credit['artist']['name']
                    break
        
        # Extract track count from media structure
        track_count = 0
        if 'media' in release:
            track_count = sum(medium.get('track-count', 0) for medium in release['media'])
        
        # Extract year from date
        release_year = ""
        if release.get('date'):
            release_year = release.get('date', '')[:4]
        
        # Build normalized interface with unified keys for cross-service compatibility
        normalized = {
            # Core metadata (available from all services) - COPIED for frontend convenience
            'title': release.get('title', ''),
            'artist': artist_name,
            'release_date': release.get('date', ''),
            'release_year': release_year,
            'track_count': track_count,
            'country': release.get('country', ''),
            
            # Cover art (unified interface for all metadata services) - COPIED for frontend convenience
            'cover_art': {
                'available': bool(cover_art_urls),
                'front_cover': cover_art_urls.get('front') if cover_art_urls else None,
                'thumbnail': cover_art_urls.get('front_250') if cover_art_urls else None,
                'urls': cover_art_urls.copy() if cover_art_urls else {}
            },
            
            # Service-specific extensions (allows services to add unique data)
            'extensions': {
                'musicbrainz': {
                    'mbid': release.get('id', ''),
                    'status': release.get('status', ''),
                    'release_group_id': release.get('release-group', {}).get('id', ''),
                    'primary_type': release.get('release-group', {}).get('primary-type', ''),
                    'secondary_types': release.get('release-group', {}).get('secondary-type-list', [])
                }
            }
        }
        
        return normalized
    
    
    async def _get_cover_art_urls(self, release_mbid: str) -> Optional[Dict[str, str]]:
        """
        Get cover art URLs from Cover Art Archive API
        Uses separate API call like reference implementation
        """
        try:
            session = await self._get_session()
            url = f"https://coverartarchive.org/release/{release_mbid}"
            
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Look for front cover first (like reference implementation)
                    for image in data.get('images', []):
                        if 'Front' in image.get('types', []):
                            thumbnails = image.get('thumbnails', {})
                            
                            # Build URLs dict with multiple sizes
                            urls = {}
                            if 'small' in thumbnails:
                                urls['front_250'] = thumbnails['small']  # 250px
                            if 'large' in thumbnails:
                                urls['front_500'] = thumbnails['large']  # 500px  
                            if image.get('image'):
                                urls['front'] = image['image']  # Original
                                urls['back'] = f"https://coverartarchive.org/release/{release_mbid}/back"  # Speculative back URL
                            
                            if urls:
                                return urls
                    
                    # If no front cover, use first available image
                    if data.get('images'):
                        image = data['images'][0]
                        thumbnails = image.get('thumbnails', {})
                        urls = {}
                        if 'small' in thumbnails:
                            urls['front_250'] = thumbnails['small']
                        if 'large' in thumbnails:
                            urls['front_500'] = thumbnails['large']
                        if image.get('image'):
                            urls['front'] = image['image']
                            urls['back'] = f"https://coverartarchive.org/release/{release_mbid}/back"
                        
                        if urls:
                            return urls
                
                elif response.status == 404:
                    # No cover art available for this release
                    return None
                else:
                    print(f"Cover Art Archive HTTP {response.status} for release {release_mbid}")
                    return None
                    
        except asyncio.TimeoutError:
            print(f"Cover Art Archive timeout for release {release_mbid}")
            return None
        except Exception as e:
            print(f"Cover Art Archive error for release {release_mbid}: {e}")
            return None
        
        return None
    
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
