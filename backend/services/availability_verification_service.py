#!/usr/bin/env python3
"""
YouTube Music Downloader - Availability Verification Service
Checks if YouTube URLs are still accessible (not deleted/private/unavailable)
"""

import asyncio
import subprocess
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from backend.core.models import Result


class AvailabilityVerificationService:
    """
    Stateless service that verifies YouTube content availability
    Uses yt-dlp to check if URLs are still accessible
    """
    
    def __init__(self):
        self.service_name = "availability_verification"
    
    async def verify_availability(self, url: str, cookie_options: Optional[Dict] = None) -> Tuple[bool, Optional[str]]:
        """
        Verify if a YouTube URL is still available
        
        Args:
            url: YouTube URL to check
            cookie_options: Cookie options from StateManager
            
        Returns:
            Tuple of (is_available, error_message)
        """
        try:
            cmd = [
                "yt-dlp",
                "--no-download",  # Don't download, just check
                "--print", "%(title)s",  # Print title if available
                "--quiet",  # Suppress progress output
                "--no-warnings",
                url
            ]
            
            # Add cookie options if provided
            if cookie_options and "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
            except asyncio.TimeoutError:
                return False, "Verification timeout"
            
            if process.returncode == 0:
                # URL is available
                return True, None
            else:
                # Parse error message
                error_msg = stderr.decode('utf-8').strip() if stderr else "Unknown error"
                
                # Common error patterns
                if "Private video" in error_msg:
                    return False, "Private video"
                elif "Video unavailable" in error_msg:
                    return False, "Video unavailable"
                elif "deleted" in error_msg.lower():
                    return False, "Video deleted"
                elif "copyright" in error_msg.lower():
                    return False, "Copyright blocked"
                elif "not available" in error_msg.lower():
                    return False, "Not available in your region"
                else:
                    return False, f"Unavailable: {error_msg[:100]}"
                    
        except Exception as e:
            print(f"AvailabilityVerificationService: Verification failed for {url}: {e}")
            return False, f"Verification error: {str(e)}"
    
    async def verify_batch(self, results: List[Result], cookie_options: Optional[Dict] = None, 
                          max_concurrent: int = 5) -> Dict[str, Tuple[bool, Optional[str]]]:
        """
        Verify availability of multiple results in parallel
        
        Args:
            results: List of Result objects to verify
            cookie_options: Cookie options from StateManager
            max_concurrent: Maximum concurrent verifications
            
        Returns:
            Dict mapping result IDs to (is_available, error_message) tuples
        """
        verification_results = {}
        
        # Create semaphore to limit concurrent verifications
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def verify_with_semaphore(result: Result):
            async with semaphore:
                is_available, error_msg = await self.verify_availability(
                    result.youtube_url, 
                    cookie_options
                )
                return result.id, is_available, error_msg
        
        # Create tasks for all results
        tasks = [verify_with_semaphore(result) for result in results]
        
        # Run verifications in parallel
        print(f"AvailabilityVerificationService: Verifying {len(results)} URLs...")
        completed = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        available_count = 0
        unavailable_count = 0
        
        for item in completed:
            if isinstance(item, tuple):
                result_id, is_available, error_msg = item
                verification_results[result_id] = (is_available, error_msg)
                
                if is_available:
                    available_count += 1
                else:
                    unavailable_count += 1
            else:
                # Exception occurred
                print(f"AvailabilityVerificationService: Verification exception: {item}")
        
        print(f"AvailabilityVerificationService: Verification complete - "
              f"{available_count} available, {unavailable_count} unavailable")
        
        return verification_results
    
    async def quick_check(self, url: str, cookie_options: Optional[Dict] = None) -> bool:
        """
        Quick availability check (just returns boolean, no error details)
        
        Args:
            url: YouTube URL to check
            cookie_options: Cookie options
            
        Returns:
            True if available, False otherwise
        """
        is_available, _ = await self.verify_availability(url, cookie_options)
        return is_available
    
    def get_service_info(self) -> Dict:
        """Get service information and status"""
        return {
            "name": self.service_name,
            "description": "Verifies YouTube content availability",
            "cookie_support": True,
            "status": "ready"
        }


class VerificationCache:
    """
    Cache for verification results to avoid repeated checks
    Managed by StateManager as part of single source of truth
    """
    
    def __init__(self, cache_duration_minutes: int = 30):
        self.cache: Dict[str, Tuple[bool, Optional[str], datetime]] = {}
        self.cache_duration = timedelta(minutes=cache_duration_minutes)
    
    def get(self, url: str) -> Optional[Tuple[bool, Optional[str]]]:
        """
        Get cached verification result if still valid
        
        Args:
            url: YouTube URL
            
        Returns:
            Tuple of (is_available, error_message) if cached and valid, None otherwise
        """
        if url in self.cache:
            is_available, error_msg, timestamp = self.cache[url]
            
            # Check if cache entry is still valid
            if datetime.now() - timestamp < self.cache_duration:
                return is_available, error_msg
            else:
                # Expired, remove from cache
                del self.cache[url]
        
        return None
    
    def set(self, url: str, is_available: bool, error_msg: Optional[str] = None):
        """
        Cache a verification result
        
        Args:
            url: YouTube URL
            is_available: Whether the URL is available
            error_msg: Optional error message if not available
        """
        self.cache[url] = (is_available, error_msg, datetime.now())
    
    def clear_expired(self):
        """Remove expired entries from cache"""
        now = datetime.now()
        expired_urls = [
            url for url, (_, _, timestamp) in self.cache.items()
            if now - timestamp >= self.cache_duration
        ]
        
        for url in expired_urls:
            del self.cache[url]
    
    def get_stats(self) -> Dict:
        """Get cache statistics"""
        self.clear_expired()  # Clean up first
        
        total_entries = len(self.cache)
        available_count = sum(1 for is_avail, _, _ in self.cache.values() if is_avail)
        unavailable_count = total_entries - available_count
        
        return {
            "total_cached": total_entries,
            "available": available_count,
            "unavailable": unavailable_count,
            "cache_duration_minutes": self.cache_duration.total_seconds() / 60
        }