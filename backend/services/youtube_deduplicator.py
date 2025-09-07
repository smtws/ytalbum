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
        
        Args:
            new_result: New result to check
            existing_results: List of existing results
            
        Returns:
            True if should add, False if duplicate
        """
        if not new_result.youtube_id:
            print(f"YouTubeDeduplicator: Rejecting result without YouTube ID: {new_result.title}")
            return False
        
        is_duplicate = YouTubeDeduplicator.check_duplicate(new_result, existing_results)
        
        if is_duplicate:
            print(f"YouTubeDeduplicator: Rejecting duplicate {new_result.youtube_id} - {new_result.title}")
            return False
        
        print(f"YouTubeDeduplicator: Accepting unique result {new_result.youtube_id} - {new_result.title}")
        return True
    
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