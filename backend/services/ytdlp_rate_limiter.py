#!/usr/bin/env python3
"""
YouTube Music Downloader - yt-dlp Rate Limiting Service
Prevents overwhelming YouTube's API with concurrent yt-dlp subprocess calls
"""

import asyncio
import time
from typing import Dict, Any


class YTDLPRateLimiter:
    """
    Centralized rate limiter for yt-dlp subprocess calls
    Prevents IP blocking and 429 errors from YouTube API
    """
    
    def __init__(self, delay: float = 0.5):
        """
        Initialize rate limiter
        
        Args:
            delay: Minimum seconds between yt-dlp calls (default: 0.5s = 2 requests/second)
        """
        self.service_name = "ytdlp_rate_limiter"
        self.rate_limit_delay = delay
        self.last_request_time = 0  # Will be set on first request
        self._lock = asyncio.Lock()  # Ensure thread-safe access in concurrent scenarios
        
    async def wait_if_needed(self) -> None:
        """
        Enforce rate limiting before making yt-dlp subprocess call
        Only waits the necessary time, not full delay period
        """
        async with self._lock:
            now = time.time()
            
            # Skip rate limiting on first request
            if self.last_request_time == 0:
                self.last_request_time = now
                return
                
            time_since_last = now - self.last_request_time
            
            if time_since_last < self.rate_limit_delay:
                sleep_time = self.rate_limit_delay - time_since_last
                print(f"YTDLPRateLimiter: Waiting {sleep_time:.3f}s (last request {time_since_last:.3f}s ago)")
                await asyncio.sleep(sleep_time)
            else:
                print(f"YTDLPRateLimiter: No wait needed ({time_since_last:.3f}s since last request)")
            
            # Update timestamp to mark this request time
            self.last_request_time = time.time()
    
    async def execute_subprocess(self, cmd: list, **kwargs) -> tuple:
        """
        Execute yt-dlp subprocess with rate limiting
        
        Args:
            cmd: Command list for subprocess (should start with "yt-dlp")
            **kwargs: Additional arguments for asyncio.create_subprocess_exec
            
        Returns:
            (stdout, stderr, returncode) tuple
        """
        if not cmd or cmd[0] != "yt-dlp":
            raise ValueError("Command must start with 'yt-dlp'")
        
        # Apply rate limiting
        await self.wait_if_needed()
        
        # Execute subprocess
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs
            )
            
            stdout, stderr = await process.communicate()
            
            return (
                stdout.decode('utf-8', errors='ignore') if stdout else '',
                stderr.decode('utf-8', errors='ignore') if stderr else '',
                process.returncode
            )
            
        except Exception as e:
            print(f"YTDLPRateLimiter: Subprocess execution failed: {e}")
            return ('', str(e), -1)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get rate limiter statistics"""
        now = time.time()
        time_since_last = now - self.last_request_time if self.last_request_time > 0 else 0
        
        return {
            "service": self.service_name,
            "rate_limit_delay": self.rate_limit_delay,
            "requests_per_second": 1.0 / self.rate_limit_delay,
            "last_request_seconds_ago": time_since_last,
            "ready_for_request": time_since_last >= self.rate_limit_delay or self.last_request_time == 0
        }
    
    def get_service_info(self) -> Dict[str, Any]:
        """Get service information"""
        return {
            'name': self.service_name,
            'description': 'yt-dlp subprocess rate limiting',
            'rate_limit': f'{1.0/self.rate_limit_delay:.1f} requests/second',
            'purpose': 'Prevent YouTube API blocking and 429 errors',
            'status': 'ready'
        }