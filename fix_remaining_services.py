#!/usr/bin/env python3
"""
Quick fix script to update all remaining search services to accept cookie_options parameter
"""

import os
import re

services_to_fix = [
    "youtube_music_search.py",
    "playlist_search.py", 
    "google_search.py",
    "direct_album_search.py",
    "genre_context_search.py",
    "alternative_title_search.py"
]

base_path = "/home/tordt/YT-Downloads/backend/services/search/"

def fix_service_file(filepath):
    """Fix a single service file to accept cookie_options parameter"""
    print(f"Fixing {filepath}...")
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Fix 1: Remove CookieService import
    content = re.sub(r'from backend\.services\.cookie_service import CookieService\n', '', content)
    
    # Fix 2: Remove self.cookie_options line from __init__
    content = re.sub(r'\s+self\.cookie_options = CookieService\.get_yt_dlp_cookie_options\(\)\n', '', content)
    
    # Fix 3: Update search method signature
    old_sig = r'async def search\(self, artist: str, album: Optional\[str\] = None\) -> List\[Result\]:'
    new_sig = r'async def search(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:'
    content = re.sub(old_sig, new_sig, content)
    
    # Fix 4: Add cookie_options parameter to docstring
    content = re.sub(r'(\s+album: Optional specific album name\n)', 
                     r'\1            cookie_options: Cookie options from StateManager (single source of truth)\n', content)
    
    # Fix 5: Add cookie_options null check at start of search method
    # Find the search method and add null check after the print statement
    search_start = content.find('print(f"')
    if search_start != -1:
        # Find end of print statement
        print_end = content.find('"))', search_start) + 3
        # Insert null check
        null_check = '\n        \n        # Use empty dict if no cookie options provided\n        if cookie_options is None:\n            cookie_options = {}\n        '
        content = content[:print_end] + null_check + content[print_end:]
    
    # Fix 6: Replace self.cookie_options with cookie_options
    content = re.sub(r'self\.cookie_options', 'cookie_options', content)
    
    # Fix 7: Fix get_service_info method - remove cookie_options reference
    content = re.sub(r'"cookie_support": bool\(cookie_options\),', '"cookie_support": True,', content)
    
    with open(filepath, 'w') as f:
        f.write(content)
    
    print(f"✅ Fixed {filepath}")

def main():
    print("🔧 Fixing remaining search services...")
    
    for service_file in services_to_fix:
        filepath = os.path.join(base_path, service_file)
        if os.path.exists(filepath):
            fix_service_file(filepath)
        else:
            print(f"❌ File not found: {filepath}")
    
    print("✅ All services fixed!")

if __name__ == "__main__":
    main()