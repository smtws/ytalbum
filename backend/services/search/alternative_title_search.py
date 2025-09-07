#!/usr/bin/env python3
"""
YouTube Music Downloader - Alternative Title Search Service
Searches with alternative artist names, misspellings, and naming conventions
"""

import asyncio
import subprocess
import json
import re
from typing import List, Optional, Dict, Any
from datetime import datetime

from backend.core.models import Result
from backend.services.youtube_deduplicator import YouTubeDeduplicator


class AlternativeTitleSearchService:
    """
    Stateless search service for alternative artist names and titles
    Handles common variations, misspellings, and alternative naming conventions
    """
    
    def __init__(self):
        self.service_name = "alternative_title_search"        
        # Common artist name transformations
        self.name_variations = {
            "with_the": lambda name: f"The {name}" if not name.lower().startswith("the ") else name,
            "without_the": lambda name: name[4:] if name.lower().startswith("the ") else name,
            "and_symbol": lambda name: name.replace(" and ", " & ").replace(" And ", " & "),
            "and_word": lambda name: name.replace(" & ", " and ").replace(" & ", " And "),
            "spaces_removed": lambda name: name.replace(" ", ""),
            "underscores": lambda name: name.replace(" ", "_"),
            "hyphens": lambda name: name.replace(" ", "-")
        }
        
        # Common misspelling patterns for metal/rock bands
        self.misspelling_patterns = {
            "double_letters": self._create_double_letter_variants,
            "y_to_i": lambda name: name.replace("y", "i").replace("Y", "I"),
            "i_to_y": lambda name: name.replace("i", "y").replace("I", "Y"),
            "ph_to_f": lambda name: name.replace("ph", "f").replace("Ph", "F"),
            "f_to_ph": lambda name: name.replace("f", "ph").replace("F", "Ph")
        }
    
    async def search(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """
        Search with alternative artist names and title variations
        
        Args:
            artist: Artist name to search for
            album: Optional specific album name
            cookie_options: Cookie options from StateManager (single source of truth)
            
        Returns:
            List of Result objects (stateless - doesn't modify state)
        """
        print(f"AlternativeTitleSearchService: Searching alternatives for '{artist}'" + 
              (f" album '{album}'" if album else ""))
        
        # Use empty dict if no cookie options provided
        if cookie_options is None:
            cookie_options = {}
        
        
        results = []
        
        try:
            # Strategy 1: Name variation searches
            variation_results = await self._search_name_variations(artist, album, cookie_options)
            results.extend(variation_results)
            
            # Strategy 2: Common misspelling searches
            misspelling_results = await self._search_common_misspellings(artist, album, cookie_options)
            results.extend(misspelling_results)
            
            # Strategy 3: Abbreviated and extended name searches
            abbreviated_results = await self._search_abbreviated_names(artist, album, cookie_options)
            results.extend(abbreviated_results)
            
            print(f"AlternativeTitleSearchService: Found {len(results)} alternative results")
            
        except Exception as e:
            print(f"AlternativeTitleSearchService: Search failed: {e}")
        
        return results
    
    async def _search_name_variations(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Search with different name variations"""
        results = []
        
        try:
            # Generate name variations
            variations = set()
            for variation_name, transform_func in self.name_variations.items():
                try:
                    variant = transform_func(artist)
                    if variant != artist and variant not in variations:
                        variations.add(variant)
                except:
                    continue
            
            # Search with each variation
            for variant in list(variations)[:4]:  # Limit to 4 variations to avoid spam
                variant_results = await self._search_with_alternative_artist(variant, album, cookie_options)
                # Only take highly relevant results for variations
                relevant = [r for r in variant_results if self._is_variation_relevant(r, artist, variant)]
                results.extend(relevant)
            
        except Exception as e:
            print(f"AlternativeTitleSearchService: Name variations search failed: {e}")
        
        return results
    
    async def _search_common_misspellings(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Search with common misspelling patterns"""
        results = []
        
        try:
            # Generate misspelling variants
            misspellings = set()
            for pattern_name, transform_func in self.misspelling_patterns.items():
                try:
                    variants = transform_func(artist)
                    if isinstance(variants, list):
                        for variant in variants:
                            if variant != artist and variant not in misspellings:
                                misspellings.add(variant)
                    else:
                        if variants != artist and variants not in misspellings:
                            misspellings.add(variants)
                except:
                    continue
            
            # Search with each misspelling
            for misspelling in list(misspellings)[:3]:  # Limit to 3 misspellings
                misspelling_results = await self._search_with_alternative_artist(misspelling, album, cookie_options)
                # Filter for relevance
                relevant = [r for r in misspelling_results if self._is_misspelling_relevant(r, artist)]
                results.extend(relevant)
            
        except Exception as e:
            print(f"AlternativeTitleSearchService: Misspellings search failed: {e}")
        
        return results
    
    async def _search_abbreviated_names(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Search with abbreviated and extended names"""
        results = []
        
        try:
            abbreviated_variants = []
            
            # Create abbreviations (if multi-word)
            words = artist.split()
            if len(words) > 1:
                # First letter abbreviation (e.g., "Iron Maiden" -> "IM")
                initials = "".join(word[0].upper() for word in words)
                abbreviated_variants.append(initials)
                
                # Partial abbreviations (e.g., "Iron Maiden" -> "I. Maiden")
                if len(words) == 2:
                    partial_abbrev = f"{words[0][0]}. {words[1]}"
                    abbreviated_variants.append(partial_abbrev)
            
            # Search with abbreviations
            for abbrev in abbreviated_variants:
                abbrev_results = await self._search_with_alternative_artist(abbrev, album, cookie_options)
                # Filter for abbreviation relevance
                relevant = [r for r in abbrev_results if self._is_abbreviation_relevant(r, artist, abbrev)]
                results.extend(relevant)
            
        except Exception as e:
            print(f"AlternativeTitleSearchService: Abbreviated names search failed: {e}")
        
        return results
    
    async def _search_with_alternative_artist(self, alternative_artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """Execute search with alternative artist name"""
        results = []
        
        try:
            if album:
                queries = [
                    f"{alternative_artist} {album}",
                    f"{alternative_artist} {album} full album"
                ]
            else:
                queries = [
                    f"{alternative_artist} album",
                    f"{alternative_artist} discography"
                ]
            
            for query in queries:
                query_results = await self._execute_search(query, alternative_artist, cookie_options)
                results.extend(query_results)
            
        except Exception as e:
            print(f"AlternativeTitleSearchService: Alternative artist '{alternative_artist}' search failed: {e}")
        
        return results
    
    async def _execute_search(self, query: str, artist: str, cookie_options: Optional[Dict[str, Any]] = None, limit: int = 6) -> List[Result]:
        """Execute a single search query"""
        results = []
        
        try:
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--default-search", f"ytsearch{limit}:",
                "--match-filter", "duration > 600",  # Only videos longer than 10 minutes
                query
            ]
            
            # Add cookie options
            if "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
            
            if process.returncode == 0 and stdout:
                lines = stdout.decode('utf-8').strip().split('\n')
                
                for line in lines:
                    try:
                        data = json.loads(line)
                        
                        # Extract thumbnail URL (prefer best quality)
                        thumbnail_url = None
                        if 'thumbnail' in data:
                            thumbnail_url = data['thumbnail']
                        elif 'thumbnails' in data and data['thumbnails']:
                            thumbnail_url = data['thumbnails'][-1].get('url')  # Last is usually best quality

                        # Extract track count (playlist_count for playlists, 1 for single videos >10min)
                        track_count = data.get('playlist_count')
                        if track_count is None:
                            # Single video that passed duration filter (>10min) - assume 1 track
                            track_count = 1

                        result = YouTubeDeduplicator.prepare_result(
                            url=data.get('webpage_url', ''),
                            title=data.get('title', ''),
                            artist=artist,
                            channel=data.get('uploader', 'Unknown'),
                            discovered_by=self.service_name,
                            thumbnail_url=thumbnail_url,
                            track_count=track_count
                        )
                        
                        if result:
                            # Lower quality score for alternative searches
                            result.quality_score = 0.6
                            results.append(result)
                            
                    except json.JSONDecodeError:
                        continue
        
        except Exception as e:
            print(f"AlternativeTitleSearchService: Query '{query}' failed: {e}")
        
        return results
    
    def _create_double_letter_variants(self, name: str) -> List[str]:
        """Create variants with doubled consonants (common in metal band names)"""
        variants = []
        consonants = "bcdfghjklmnpqrstvwxyz"
        
        # Try doubling single consonants
        for i, char in enumerate(name.lower()):
            if char in consonants and (i == 0 or name[i-1].lower() != char):
                variant = name[:i] + char + name[i:]
                variants.append(variant)
                if len(variants) >= 3:  # Limit variants
                    break
        
        return variants
    
    def _is_variation_relevant(self, result: Result, original_artist: str, variant_artist: str) -> bool:
        """Check if result is relevant for name variation"""
        title_lower = result.title.lower()
        channel_lower = result.channel.lower()
        text = f"{title_lower} {channel_lower}"
        
        original_lower = original_artist.lower()
        variant_lower = variant_artist.lower()
        
        # Must contain either original or variant
        has_original = original_lower in text
        has_variant = variant_lower in text
        
        if not (has_original or has_variant):
            return False
        
        # Bonus for album indicators
        album_indicators = ['full album', 'album', 'playlist', 'discography']
        has_album_indicator = any(indicator in title_lower for indicator in album_indicators)
        
        if has_album_indicator:
            result.quality_score = 0.8
            return True
        
        return True
    
    def _is_misspelling_relevant(self, result: Result, original_artist: str) -> bool:
        """Check if result is relevant for misspelling search"""
        title_lower = result.title.lower()
        original_lower = original_artist.lower()
        
        # Basic relevance check - should contain some form of the artist name
        # (This is lenient since we're dealing with misspellings)
        words = original_lower.split()
        if len(words) > 0:
            # At least one word should be similar
            title_words = title_lower.split()
            for orig_word in words:
                for title_word in title_words:
                    if len(orig_word) > 3 and orig_word[:3] in title_word:
                        return True
        
        return False
    
    def _is_abbreviation_relevant(self, result: Result, original_artist: str, abbreviation: str) -> bool:
        """Check if result is relevant for abbreviation search"""
        title_lower = result.title.lower()
        channel_lower = result.channel.lower()
        text = f"{title_lower} {channel_lower}"
        
        original_lower = original_artist.lower()
        abbrev_lower = abbreviation.lower()
        
        # Must contain abbreviation or original
        has_original = original_lower in text
        has_abbrev = abbrev_lower in text
        
        if not (has_original or has_abbrev):
            return False
        
        # Album indicators boost relevance
        album_indicators = ['album', 'playlist', 'discography']
        has_album_indicator = any(indicator in title_lower for indicator in album_indicators)
        
        if has_album_indicator:
            result.quality_score = 0.7
        
        return True
    
    def get_service_info(self) -> Dict[str, Any]:
        """Get service information and status"""
        return {
            "name": self.service_name,
            "description": "Alternative artist names, misspellings, and naming variations",
            "cookie_support": True,
            "search_strategies": ["name variations", "common misspellings", "abbreviations"],
            "quality_score": 0.6,  # Lower priority alternative method
            "status": "ready"
        }