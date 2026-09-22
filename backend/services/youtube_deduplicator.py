#!/usr/bin/env python3
"""
YouTube Music Downloader - YouTube ID Deduplicator
Filters out YouTube ID duplicates BEFORE verification and BEFORE adding to AppState
"""

import copy
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
    async def fix_playlist_track_count(result: Result, ytdlp_executor) -> Result:
        """
        Fix track count for playlist results that have incorrect counts
        Only called when needed to avoid unnecessary API calls
        """
        if not result.youtube_id or not result.youtube_id.startswith('playlist:'):
            return result
        
        # Always verify playlist track counts since they can be contaminated
        # For single videos, only fix if track count seems unreasonably high (likely contamination)  
        # For playlist URLs, always verify since they're more prone to contamination
        print(f"YouTubeDeduplicator: Checking track count for {result.title} ({result.track_count} tracks)")
        print(f"YouTubeDeduplicator: URL: {result.youtube_url}")
        
        if 'playlist?list=' not in result.youtube_url:
            # Single video - only fix if track count seems wrong (>20 is very suspicious for a single video)
            if result.track_count <= 20:
                print(f"YouTubeDeduplicator: Skipping single video with reasonable track count: {result.track_count}")
                return result  # Reasonable track count for single video with chapters
            else:
                print(f"YouTubeDeduplicator: Single video with suspicious track count {result.track_count}, will verify")
        else:
            print(f"YouTubeDeduplicator: Playlist URL, will verify track count")
        
        try:
            cmd = [
                "yt-dlp",
                "--flat-playlist", 
                "--print", "%(playlist_count)s",
                result.youtube_url
            ]
            
            stdout, stderr, returncode = await ytdlp_executor(cmd)
            if returncode == 0 and stdout:
                lines = stdout.strip().split('\n')
                # Take the first valid number from the output
                for line in lines:
                    if line.strip().isdigit():
                        actual_count = int(line.strip())
                        if actual_count != result.track_count:
                            print(f"YouTubeDeduplicator: Fixed track count from {result.track_count} to {actual_count} for {result.youtube_url}")
                        else:
                            print(f"YouTubeDeduplicator: Verified track count {actual_count} for {result.youtube_url}")
                        result.track_count = actual_count
                        break
        except Exception as e:
            print(f"YouTubeDeduplicator: Failed to fix track count for {result.youtube_url}: {e}")
        
        return result
    
    @staticmethod
    def should_add_result(new_result: Result, existing_results: List[Result]) -> bool:
        """
        Determine if new result should be added (not a duplicate)
        Uses simple insert/replace logic - no merging to prevent data contamination
        
        Args:
            new_result: New result to check
            existing_results: List of existing results
            
        Returns:
            True if should add/replace, False if should reject
        """
        if not new_result.youtube_id:
            print(f"YouTubeDeduplicator: Rejecting result without YouTube ID: {new_result.title}")
            return False
        
        # Check for exact duplicate
        existing_duplicate = YouTubeDeduplicator.find_duplicate(new_result, existing_results)
        
        if existing_duplicate:
            # Simple insert/replace logic - no merging to prevent contamination
            # For now, just keep the first result found (existing wins)
            # TODO: Later we can implement smarter replacement based on quality metrics
            print(f"YouTubeDeduplicator: Found duplicate {new_result.youtube_id} - keeping existing result")
            print(f"YouTubeDeduplicator: Existing: {existing_duplicate.title}")
            print(f"YouTubeDeduplicator: Rejecting: {new_result.title}")
            return False  # Keep existing, reject new
        
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
        
        # Track count comparison removed - higher count doesn't mean better quality
        # Official albums are often smaller than compilations/remixes
        # Let other factors (quality_score, thumbnails, etc.) determine priority
        
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
        Preserves ANY existing metadata regardless of format (pre-normalization stage)
        
        Args:
            new_result: New result with potentially better raw data
            existing_result: Existing result with metadata to preserve
            
        Returns:
            New result with preserved metadata
        """
        # Preserve ANY existing metadata fields (agnostic to format)
        if existing_result.verification_metadata:
            new_result.verification_metadata = copy.deepcopy(existing_result.verification_metadata)
            print(f"YouTubeDeduplicator: Preserved verification_metadata for {new_result.youtube_id}")
        
        if existing_result.normalized:
            new_result.normalized = copy.deepcopy(existing_result.normalized)
            print(f"YouTubeDeduplicator: Preserved normalized metadata for {new_result.youtube_id}")
        
        if existing_result.metadata:
            new_result.metadata = copy.deepcopy(existing_result.metadata)
            print(f"YouTubeDeduplicator: Preserved metadata for {new_result.youtube_id}")
        
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