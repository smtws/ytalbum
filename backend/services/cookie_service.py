#!/usr/bin/env python3
"""
YouTube Music Downloader - Cookie Detection Service
Detects which browsers are installed so yt-dlp can use their cookies
"""

import subprocess
import os
from typing import Optional, List, Dict, Any

from backend.core.models import CookieInfo


class CookieService:
    """
    Stateless service for browser detection
    Helps yt-dlp access age-restricted content by detecting available browsers
    """
    
    
    # Browser executables to check for
    BROWSER_EXECUTABLES = {
        "chrome": ["google-chrome", "chrome", "google-chrome-stable"],
        "chromium": ["chromium", "chromium-browser"],
        "firefox": ["firefox"],
        "edge": ["microsoft-edge", "msedge"]
    }
    
    @staticmethod
    def find_installed_browsers() -> List[str]:
        """
        Find which browsers are installed on the system
        
        Returns:
            List of browser names that are installed
        """
        installed_browsers = []
        
        for browser_name, executables in CookieService.BROWSER_EXECUTABLES.items():
            for executable in executables:
                try:
                    # Check if browser is in PATH
                    result = subprocess.run(
                        ["which", executable], 
                        capture_output=True, 
                        text=True, 
                        timeout=5
                    )
                    if result.returncode == 0:
                        installed_browsers.append(browser_name)
                        print(f"CookieService: Found {browser_name} at {result.stdout.strip()}")
                        break  # Found this browser, no need to check other executables
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue
        
        return installed_browsers
    
    @staticmethod
    def get_recommended_browser() -> Optional[str]:
        """
        Get the recommended browser for yt-dlp cookie extraction
        
        Returns:
            Browser name that yt-dlp can use, or None if none found
        """
        installed_browsers = CookieService.find_installed_browsers()
        
        # Priority order: Chrome, Chromium, Firefox, Edge
        priority_order = ["chrome", "chromium", "firefox", "edge"]
        
        for browser in priority_order:
            if browser in installed_browsers:
                print(f"CookieService: Recommending {browser} for yt-dlp")
                return browser
        
        if installed_browsers:
            # Use first available if none match priority
            browser = installed_browsers[0]
            print(f"CookieService: Using {browser} for yt-dlp")
            return browser
        
        print("CookieService: No browsers found for cookie extraction")
        return None
    
    @staticmethod
    def test_browser_cookies(browser: str) -> bool:
        """
        Test if yt-dlp can extract cookies from specified browser
        
        Args:
            browser: Browser name to test
            
        Returns:
            True if cookies can be extracted, False otherwise
        """
        try:
            cmd = [
                "yt-dlp",
                "--cookies-from-browser", browser,
                "--no-download",
                "--print", "title",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            
            if result.returncode == 0 and result.stdout.strip():
                print(f"CookieService: {browser} cookies work - got title: {result.stdout.strip()}")
                return True
            else:
                print(f"CookieService: {browser} cookies failed: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"CookieService: {browser} cookie test error: {e}")
            return False
    
    @staticmethod
    def get_yt_dlp_cookie_options() -> Dict[str, Any]:
        """
        Get yt-dlp options for cookie handling
        
        Returns:
            Dict with yt-dlp cookie options
        """
        options = {}
        
        # Get recommended browser
        browser = CookieService.get_recommended_browser()
        
        if browser:
            options["cookiesfrombrowser"] = (browser, None, None, None)
            print(f"CookieService: Using {browser} cookies for yt-dlp")
        else:
            print("CookieService: No browsers available for yt-dlp cookies")
        
        return options
    
    @staticmethod
    def detect_and_create_cookie_info() -> CookieInfo:
        """
        Detect browsers and create CookieInfo object for AppState
        
        Returns:
            CookieInfo object with current detection results
        """
        installed_browsers = CookieService.find_installed_browsers()
        recommended_browser = CookieService.get_recommended_browser()
        cookie_options = CookieService.get_yt_dlp_cookie_options()
        
        # Test yt-dlp compatibility
        yt_dlp_compatible = bool(cookie_options)
        
        cookie_info = CookieInfo(
            browsers_detected=installed_browsers,
            recommended_browser=recommended_browser,
            cookie_files_count=0,  # Not needed anymore - yt-dlp handles this
            cookie_support_available=bool(recommended_browser),
            yt_dlp_compatible=yt_dlp_compatible
        )
        
        print(f"CookieService: Detected {len(installed_browsers)} browsers: {installed_browsers}")
        if recommended_browser:
            print(f"CookieService: Recommended browser for yt-dlp: {recommended_browser}")
        
        return cookie_info