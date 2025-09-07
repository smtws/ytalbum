#!/usr/bin/env python3
"""
YouTube Music Downloader - Cookie Detection Service
Detects and manages cookies for yt-dlp to work properly with age-restricted/private content
"""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from backend.core.models import CookieInfo


class CookieService:
    """
    Stateless service for cookie detection and management
    Helps yt-dlp access age-restricted and private YouTube content
    """
    
    # Common cookie file locations for different browsers
    COOKIE_PATHS = {
        "chrome": [
            "~/.config/google-chrome/Default/Cookies",
            "~/Library/Application Support/Google/Chrome/Default/Cookies",  # macOS
            "%LOCALAPPDATA%/Google/Chrome/User Data/Default/Cookies",  # Windows
        ],
        "firefox": [
            "~/.mozilla/firefox/*/cookies.sqlite",
            "~/Library/Application Support/Firefox/Profiles/*/cookies.sqlite",  # macOS
            "%APPDATA%/Mozilla/Firefox/Profiles/*/cookies.sqlite",  # Windows
        ],
        "chromium": [
            "~/.config/chromium/Default/Cookies",
            "~/snap/chromium/common/chromium/Default/Cookies",  # Snap
        ],
        "edge": [
            "~/.config/microsoft-edge/Default/Cookies",
            "%LOCALAPPDATA%/Microsoft/Edge/User Data/Default/Cookies",  # Windows
        ]
    }
    
    @staticmethod
    def find_cookie_files() -> Dict[str, List[str]]:
        """
        Find available cookie files from different browsers
        
        Returns:
            Dict mapping browser names to found cookie file paths
        """
        found_cookies = {}
        
        for browser, paths in CookieService.COOKIE_PATHS.items():
            browser_cookies = []
            
            for path_template in paths:
                # Expand user path and environment variables
                expanded_path = os.path.expanduser(os.path.expandvars(path_template))
                
                # Handle wildcard paths (like Firefox profiles)
                if "*" in expanded_path:
                    import glob
                    matches = glob.glob(expanded_path)
                    for match in matches:
                        if os.path.exists(match):
                            browser_cookies.append(match)
                else:
                    if os.path.exists(expanded_path):
                        browser_cookies.append(expanded_path)
            
            if browser_cookies:
                found_cookies[browser] = browser_cookies
        
        return found_cookies
    
    @staticmethod
    def get_recommended_cookie_file() -> Optional[str]:
        """
        Get the recommended cookie file for yt-dlp
        
        Returns:
            Path to recommended cookie file, or None if none found
        """
        found_cookies = CookieService.find_cookie_files()
        
        # Priority order: Chrome, Chromium, Firefox, Edge
        priority_order = ["chrome", "chromium", "firefox", "edge"]
        
        for browser in priority_order:
            if browser in found_cookies and found_cookies[browser]:
                cookie_file = found_cookies[browser][0]  # Take first found
                print(f"CookieService: Recommending {browser} cookies: {cookie_file}")
                return cookie_file
        
        print("CookieService: No browser cookies found")
        return None
    
    @staticmethod
    def create_yt_dlp_cookies_txt(output_path: str = "cookies.txt") -> bool:
        """
        Create a cookies.txt file for yt-dlp from browser cookies
        
        Args:
            output_path: Where to save the cookies.txt file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Try to use yt-dlp's built-in cookie extraction
            import subprocess
            
            cookie_file = CookieService.get_recommended_cookie_file()
            if not cookie_file:
                return False
            
            # Extract cookies using yt-dlp
            cmd = [
                "yt-dlp",
                "--cookies-from-browser", "chrome",  # or detected browser
                "--print-json",
                "--dump-single-json",
                "--no-download",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Test video
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                print(f"CookieService: Successfully extracted cookies")
                return True
            else:
                print(f"CookieService: Cookie extraction failed: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"CookieService: Cookie extraction error: {e}")
            return False
    
    @staticmethod
    def get_yt_dlp_cookie_options() -> Dict[str, Any]:
        """
        Get yt-dlp options for cookie handling
        
        Returns:
            Dict with yt-dlp cookie options
        """
        options = {}
        
        # Try to detect and use browser cookies
        found_cookies = CookieService.find_cookie_files()
        
        if "chrome" in found_cookies:
            options["cookiesfrombrowser"] = ("chrome", None, None, None)
            print("CookieService: Using Chrome cookies for yt-dlp")
        elif "chromium" in found_cookies:
            options["cookiesfrombrowser"] = ("chromium", None, None, None)
            print("CookieService: Using Chromium cookies for yt-dlp")
        elif "firefox" in found_cookies:
            options["cookiesfrombrowser"] = ("firefox", None, None, None)
            print("CookieService: Using Firefox cookies for yt-dlp")
        else:
            print("CookieService: No browser cookies available for yt-dlp")
        
        return options
    
    @staticmethod
    def test_cookie_access(test_url: str = "https://www.youtube.com/watch?v=dQw4w9WgXcQ") -> bool:
        """
        Test if yt-dlp can access YouTube content with current cookie setup
        
        Args:
            test_url: YouTube URL to test access
            
        Returns:
            True if access successful, False otherwise
        """
        try:
            import subprocess
            
            # Get cookie options
            cookie_options = CookieService.get_yt_dlp_cookie_options()
            
            cmd = ["yt-dlp", "--no-download", "--print", "title", test_url]
            
            # Add cookie options if available
            if "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            
            if result.returncode == 0 and result.stdout.strip():
                print(f"CookieService: Cookie test successful - got title: {result.stdout.strip()}")
                return True
            else:
                print(f"CookieService: Cookie test failed: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"CookieService: Cookie test error: {e}")
            return False
    
    @staticmethod
    def get_status_report() -> Dict[str, Any]:
        """
        Get comprehensive status report of cookie availability
        
        Returns:
            Status report with cookie detection results
        """
        found_cookies = CookieService.find_cookie_files()
        recommended = CookieService.get_recommended_cookie_file()
        cookie_options = CookieService.get_yt_dlp_cookie_options()
        
        return {
            "browsers_found": list(found_cookies.keys()),
            "total_cookie_files": sum(len(files) for files in found_cookies.values()),
            "recommended_file": recommended,
            "yt_dlp_options": cookie_options,
            "cookie_support_available": bool(recommended),
            "details": found_cookies
        }
    
    @staticmethod
    def detect_and_create_cookie_info() -> CookieInfo:
        """
        Detect cookies and create CookieInfo object for AppState
        
        Returns:
            CookieInfo object with current detection results
        """
        found_cookies = CookieService.find_cookie_files()
        recommended = CookieService.get_recommended_cookie_file()
        cookie_options = CookieService.get_yt_dlp_cookie_options()
        
        # Determine recommended browser from file path
        recommended_browser = None
        if recommended:
            for browser, paths in CookieService.COOKIE_PATHS.items():
                for path_template in paths:
                    expanded = os.path.expanduser(os.path.expandvars(path_template))
                    if "*" not in expanded and recommended == expanded:
                        recommended_browser = browser
                        break
                if recommended_browser:
                    break
        
        # Test yt-dlp compatibility
        yt_dlp_compatible = bool(cookie_options)
        
        cookie_info = CookieInfo(
            browsers_detected=list(found_cookies.keys()),
            recommended_browser=recommended_browser,
            cookie_files_count=sum(len(files) for files in found_cookies.values()),
            cookie_support_available=bool(recommended),
            yt_dlp_compatible=yt_dlp_compatible
        )
        
        print(f"CookieService: Detected {len(found_cookies)} browsers with {cookie_info.cookie_files_count} cookie files")
        if recommended_browser:
            print(f"CookieService: Recommended browser: {recommended_browser}")
        
        return cookie_info