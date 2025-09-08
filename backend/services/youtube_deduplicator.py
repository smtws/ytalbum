#!/usr/bin/env python3
"""
YouTube Music Downloader - YouTube ID Deduplicator
Filters out YouTube ID duplicates BEFORE verification and BEFORE adding to AppState
"""

import re
from typing import List, Optional
from urllib.parse import urlparse, parse_qs

from backend.core.models import Result


class YouTubeDeduplicator:
    """
    Stateless service for YouTube ID deduplication
    Prevents duplicate results from entering the AppState
    """
    
    @staticmethod
    def extract_youtube_id(url: str) -> Optional[str]:
        """
        Extract YouTube video or playlist ID from various YouTube URL formats
        
        Supported formats:
        - https://www.youtube.com/watch?v=dQw4w9WgXcQ (video)
        - https://youtu.be/dQw4w9WgXcQ (video)
        - https://youtube.com/watch?v=dQw4w9WgXcQ (video)
        - https://m.youtube.com/watch?v=dQw4w9WgXcQ (video)
        - https://www.youtube.com/playlist?list=PLxxxxxxxxx (playlist)
        """
        if not url:
            return None
        
        # Handle playlist URLs - our primary target format
        if "youtube.com/playlist" in url:
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            if 'list' in query_params:
                playlist_id = query_params['list'][0]
                # YouTube playlist IDs are typically 34 characters starting with PL
                if len(playlist_id) >= 10:  # Allow various playlist ID lengths
                    return f"playlist:{playlist_id}"  # Prefix to distinguish from video IDs
        
        # Handle youtu.be short links
        if "youtu.be/" in url:
            match = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", url)
            if match:
                return match.group(1)
        
        # Handle youtube.com watch URLs
        if "youtube.com/watch" in url:
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            if 'v' in query_params:
                video_id = query_params['v'][0]
                if len(video_id) == 11:  # YouTube IDs are always 11 characters
                    return video_id
        
        # Handle embed URLs
        if "youtube.com/embed/" in url:
            match = re.search(r"youtube\.com/embed/([a-zA-Z0-9_-]{11})", url)
            if match:
                return match.group(1)
        
        return None
    
    @staticmethod
    def check_duplicate(new_result: Result, existing_results: List[Result]) -> bool:
        """
        Check if new_result is a duplicate based on YouTube ID
        
        Args:
            new_result: New result to check
            existing_results: List of existing results
            
        Returns:
            True if duplicate found, False if unique
        """
        if not new_result.youtube_id:
            return False  # Can't deduplicate without ID
        
        for existing in existing_results:
            if existing.youtube_id == new_result.youtube_id:
                print(f"YouTubeDeduplicator: Found duplicate - {new_result.youtube_id}")
                return True
        
        return False
    
    @staticmethod
    def prepare_result(url: str, title: str, artist: str, channel: str, 
                      discovered_by: str = "", **kwargs) -> Optional[Result]:
        """
        Prepare a Result object with extracted YouTube ID
        
        Args:
            url: YouTube URL
            title: Video title
            artist: Artist name
            channel: Channel name
            discovered_by: Name of search service that found this
            **kwargs: Additional Result fields
            
        Returns:
            Result object with youtube_id set, or None if invalid URL
        """
        youtube_id = YouTubeDeduplicator.extract_youtube_id(url)
        if not youtube_id:
            print(f"YouTubeDeduplicator: Could not extract ID from URL: {url}")
            return None
        
        # Create result with extracted ID
        result_data = {
            "youtube_url": url,
            "youtube_id": youtube_id,
            "title": title,
            "artist": artist,
            "channel": channel,
            "discovered_by": discovered_by,
            **kwargs
        }
        
        return Result(**result_data)
    
    @staticmethod
    def should_add_result(new_result: Result, existing_results: List[Result]) -> bool:
        """
        Determine if new result should be added (not a duplicate)
        Prioritizes results with better metadata (higher track count, thumbnails)
        
        Args:
            new_result: New result to check
            existing_results: List of existing results
            
        Returns:
            True if should add, False if duplicate
        """
        if not new_result.youtube_id:
            print(f"YouTubeDeduplicator: Rejecting result without YouTube ID: {new_result.title}")
            return False
        
        # Check for exact duplicate
        existing_duplicate = YouTubeDeduplicator.find_duplicate(new_result, existing_results)
        
        if existing_duplicate:
            # Compare metadata quality to decide which to keep
            new_is_better = YouTubeDeduplicator._is_better_quality(new_result, existing_duplicate)
            
            if new_is_better:
                print(f"YouTubeDeduplicator: Found duplicate {new_result.youtube_id} - {new_result.title}")
                print(f"YouTubeDeduplicator: New result has better metadata - SHOULD REPLACE existing")
                return "replace"  # Special return value for replacement
            else:
                print(f"YouTubeDeduplicator: Rejecting duplicate {new_result.youtube_id} - existing has better metadata")
                return False
        
        print(f"YouTubeDeduplicator: Accepting unique result {new_result.youtube_id} - {new_result.title}")
        return True
    
    @staticmethod
    def find_duplicate(new_result: Result, existing_results: List[Result]) -> Optional[Result]:
        """Find existing duplicate result by YouTube ID"""
        for existing in existing_results:
            if existing.youtube_id == new_result.youtube_id:
                return existing
        return None
    
    @staticmethod
    def _is_better_quality(new_result: Result, existing_result: Result) -> bool:
        """
        Compare metadata quality between two results
        Returns True if new_result has better quality metadata
        
        NOTE: Does NOT consider normalized/verification metadata in quality calculation
        Only compares raw discovery metadata to ensure fair comparison
        """
        new_score = 0
        existing_score = 0
        
        # Track count comparison (higher is better)
        new_tracks = new_result.track_count or 0
        existing_tracks = existing_result.track_count or 0
        
        if new_tracks > existing_tracks:
            new_score += 3
        elif existing_tracks > new_tracks:
            existing_score += 3
        
        # Thumbnail availability (having thumbnail is better)
        if new_result.thumbnail_url and not existing_result.thumbnail_url:
            new_score += 2
        elif existing_result.thumbnail_url and not new_result.thumbnail_url:
            existing_score += 2
        
        # Quality score comparison (raw quality only, not enriched)
        if new_result.quality_score > existing_result.quality_score:
            new_score += 1
        elif existing_result.quality_score > new_result.quality_score:
            existing_score += 1
        
        return new_score > existing_score
    
    @staticmethod
    def merge_with_existing_metadata(new_result: Result, existing_result: Result) -> Result:
        """
        Merge new result with existing metadata preservation
        Keeps normalized and verification metadata from existing result
        
        Args:
            new_result: New result with potentially better raw data
            existing_result: Existing result with metadata to preserve
            
        Returns:
            New result with preserved metadata
        """
        # Preserve these metadata fields from existing result
        if existing_result.verification_metadata:
            # Check if existing has normalization data
            if 'normalized_title' in existing_result.verification_metadata:
                # Preserve all normalization and enrichment data
                new_result.verification_metadata = existing_result.verification_metadata.copy()
                print(f"YouTubeDeduplicator: Preserved normalization metadata for {new_result.youtube_id}")
            
            # Also preserve any MusicBrainz or other enrichment data
            if any(k.startswith('mb_') or k == 'mbid' for k in existing_result.verification_metadata.keys()):
                if not new_result.verification_metadata:
                    new_result.verification_metadata = {}
                mb_metadata = {
                    k: v for k, v in existing_result.verification_metadata.items()
                    if k.startswith('mb_') or k == 'mbid' or k == 'service'
                }
                new_result.verification_metadata.update(mb_metadata)
                print(f"YouTubeDeduplicator: Preserved MusicBrainz metadata for {new_result.youtube_id}")
        
        # Preserve verification status if already verified
        if existing_result.verified is not None:
            new_result.verified = existing_result.verified
        
        return new_result
    
    @staticmethod
    def get_statistics(results: List[Result]) -> dict:
        """Get deduplication statistics"""
        unique_ids = set()
        duplicate_count = 0
        
        for result in results:
            if result.youtube_id:
                if result.youtube_id in unique_ids:
                    duplicate_count += 1
                else:
                    unique_ids.add(result.youtube_id)
        
        return {
            "unique_results": len(unique_ids),
            "total_processed": len(results),
            "duplicates_found": duplicate_count,
            "duplicate_rate": (duplicate_count / len(results)) * 100 if results else 0
        }