#!/usr/bin/env python3
"""
YouTube Music Downloader - Normalization Service
Normalizes titles and artist names for optimal metadata matching
"""

import copy
import re
from typing import Dict, Tuple, Optional, List
from difflib import SequenceMatcher
from datetime import datetime


class NormalizationService:
    """
    Stateless service for normalizing titles and artist names
    Optimizes metadata search and matching accuracy
    """
    
    # Common YouTube/streaming suffixes to remove
    YOUTUBE_SUFFIXES = [
        '(Official Video)', '[Official Video]', '- Official Video',
        '(Official Audio)', '[Official Audio]', '- Official Audio',  
        '(Official Music Video)', '[Official Music Video]',
        '(Lyric Video)', '[Lyric Video]', '- Lyric Video',
        '(Lyrics)', '[Lyrics]', '- Lyrics',
        '(Visualizer)', '[Visualizer]', '- Visualizer',
        '(HD)', '[HD]', '(HQ)', '[HQ]',
        '(4K)', '[4K]', '(1080p)', '[1080p]',
        '(FULL ALBUM)', '[FULL ALBUM]', '- FULL ALBUM',
        '(Complete Album)', '[Complete Album]', 
        '(COMPLETE)', '[COMPLETE]',
        'FULL ALBUM -', 'Complete Album -',
        # Channel/playlist specific
        '(Official Channel)', '[Official Channel]', '- Official Channel',
        '(official Channel)', '[official Channel]', '- official Channel',
        'Official Videos', 'official Videos', 'Official videos', 'official videos',
        'Official Playlist', 'official Playlist', 
        'Music Videos', 'music Videos', 'Music videos', 'music videos',
        '- Videos', '- videos',
        '(Videos)', '[Videos]', '(videos)', '[videos]',
        '(Official Videos)', '[Official Videos]', '(official Videos)', '[official Videos]',
        '(Official Full Album Lyric Videos)', '[Official Full Album Lyric Videos]'
    ]
    
    # Edition/version indicators to remove for matching
    EDITION_TAGS = [
        '(Remastered)', '[Remastered]', '- Remastered',
        '(Deluxe Edition)', '[Deluxe Edition]', '- Deluxe Edition',
        '(Deluxe)', '[Deluxe]', '- Deluxe',
        '(Expanded Edition)', '[Expanded Edition]',
        '(Anniversary Edition)', '[Anniversary Edition]',
        '(Special Edition)', '[Special Edition]',
        '(Bonus Track Version)', '[Bonus Track Version]',
        '(Extended)', '[Extended]',
        '(Radio Edit)', '[Radio Edit]'
    ]
    
    # Artist name cleanups
    ARTIST_SUFFIXES = [
        ' - Topic',  # YouTube Music auto-generated
        ' VEVO',
        'VEVO',
        ' Official',
        ' (Official)',
        ' Music',
        ' Band'
    ]
    
    @staticmethod
    def normalize_album_title(title: str, artist_name: str = "") -> str:
        """
        Normalize album title for metadata matching
        Intelligently handles artist name deduplication
        
        Args:
            title: Raw album title from YouTube
            artist_name: Artist name to avoid over-normalization
            
        Returns:
            Normalized title for MusicBrainz/metadata matching
        """
        if not title:
            return ""
            
        original_title = title
        normalized = title
        
        # Remove YouTube-specific suffixes (case-insensitive, word boundary aware)
        for suffix in NormalizationService.YOUTUBE_SUFFIXES:
            # Create all case variations
            suffixes_to_check = [suffix, suffix.upper(), suffix.lower(), suffix.title()]
            
            for suffix_variant in suffixes_to_check:
                # Only remove if it's at the end of the string
                if normalized.endswith(suffix_variant):
                    normalized = normalized[:-len(suffix_variant)].strip()
                # Or if it's preceded by space and followed by end of string or punctuation
                elif f" {suffix_variant}" in normalized:
                    # Check if it's at the end or followed by punctuation/whitespace
                    suffix_pos = normalized.find(f" {suffix_variant}")
                    after_suffix = normalized[suffix_pos + len(f" {suffix_variant}"):]
                    if not after_suffix or after_suffix[0] in " .,;:!?()[]{}":
                        normalized = normalized.replace(f" {suffix_variant}", "", 1).strip()
                # Or if it starts the string and is followed by space/punctuation  
                elif normalized.startswith(f"{suffix_variant} "):
                    normalized = normalized[len(suffix_variant):].strip()
        
        # Remove edition/version tags (exact matches only)
        for tag in NormalizationService.EDITION_TAGS:
            # Create all case variations
            tags_to_check = [tag, tag.upper(), tag.lower(), tag.title()]
            
            for tag_variant in tags_to_check:
                # Only remove if it's at the end of the string or properly separated
                if normalized.endswith(tag_variant):
                    normalized = normalized[:-len(tag_variant)]
                elif f" {tag_variant}" in normalized:
                    normalized = normalized.replace(f" {tag_variant}", "")
                elif normalized.startswith(f"{tag_variant} "):
                    normalized = normalized[len(tag_variant) + 1:]
        
        # Remove year in parentheses or brackets (e.g., "(2021)", "[1969]")
        normalized = re.sub(r'[\(\[]?\d{4}[\)\]]?', '', normalized)
        
        # Clean up multiple spaces and trim
        normalized = ' '.join(normalized.split())
        
        # Remove trailing/leading punctuation
        normalized = normalized.strip(' -–—.,;:()[]{}|/\\')
        
        # Smart artist name deduplication
        if artist_name and normalized:
            normalized = NormalizationService._smart_artist_deduplication(normalized, artist_name)
        
        # If normalization resulted in too short a title, keep more of the original
        if len(normalized) < 3 and len(original_title) > 10:
            # Try a gentler normalization - just remove the most obvious YouTube tags
            gentle_normalized = original_title
            for suffix in ['(Official Video)', '(Full Album)', '- Official', 'OFFICIAL']:
                gentle_normalized = gentle_normalized.replace(suffix, '')
            gentle_normalized = ' '.join(gentle_normalized.split()).strip(' -–—.,;:()[]{}|/\\')
            if len(gentle_normalized) >= 3:
                return gentle_normalized
        
        return normalized if normalized else original_title
    
    @staticmethod
    def _smart_artist_deduplication(title: str, artist_name: str) -> str:
        """
        Intelligently remove artist name from title without over-aggressive deduplication
        Preserves self-titled albums and avoids over-normalization
        
        Args:
            title: Normalized title 
            artist_name: Artist name to potentially remove
            
        Returns:
            Title with smart artist deduplication
        """
        if not artist_name or not title:
            return title
        
        # Clean artist name for comparison
        clean_artist = NormalizationService.normalize_artist_name(artist_name)
        
        # Check if title starts with "ARTIST - ALBUM" pattern
        if title.upper().startswith(clean_artist.upper() + ' - '):
            potential_album = title[len(clean_artist) + 3:].strip()
            
            # Check if this is a self-titled album (Artist - Artist)
            if potential_album.upper() == clean_artist.upper():
                # This is self-titled, normalize to "Artist - Artist" format
                return f"{clean_artist} - {clean_artist}"
            
            # Check for remaining generic words that indicate this isn't a real album
            remaining_generic_words = ['best', 'hits', 'collection', 'greatest']
            potential_album_words = potential_album.lower().split()
            
            # If the album part still contains generic compilation words, keep the full title
            if any(word in remaining_generic_words for word in potential_album_words):
                return title
            
            # If we have substantial album content, format as "Artist - Album"
            if len(potential_album) >= 2:
                return f"{clean_artist} - {potential_album}"
            else:
                # Too short after normalization, keep original
                return title
        
        # Check for "ARTIST ARTIST" duplication without separator (rare case)
        if title.upper().startswith(clean_artist.upper() + ' ' + clean_artist.upper()) and len(title.split()) == 2:
            # This is likely "Sabaton Sabaton" -> keep as "Sabaton - Sabaton" 
            return f"{clean_artist} - {clean_artist}"
        
        return title
    
    @staticmethod
    def normalize_artist_name(artist: str) -> str:
        """
        Normalize artist name for metadata matching
        
        Args:
            artist: Raw artist name from YouTube channel
            
        Returns:
            Normalized artist name
        """
        if not artist:
            return ""
            
        normalized = artist
        
        # Remove YouTube channel suffixes
        for suffix in NormalizationService.ARTIST_SUFFIXES:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
        
        # Handle "The" prefix normalization
        # e.g., "Beatles, The" -> "The Beatles"
        if ', The' in normalized:
            normalized = 'The ' + normalized.replace(', The', '')
        elif ', the' in normalized:
            normalized = 'The ' + normalized.replace(', the', '')
        
        # Remove special characters but keep essential ones
        # Keep: apostrophes (D'Angelo), periods (R.E.M.), hyphens (Jay-Z)
        # normalized = re.sub(r'[^\w\s\'\.\-]', '', normalized)
        
        # Clean whitespace
        normalized = ' '.join(normalized.split())
        normalized = normalized.strip()
        
        return normalized
    
    @staticmethod
    def normalize_track_title(title: str) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Normalize track title and extract features/remix info
        
        Args:
            title: Raw track title
            
        Returns:
            Tuple of (normalized_title, featured_artists, version_info)
        """
        if not title:
            return "", None, None
            
        normalized = title
        featured_artists = None
        version_info = None
        
        # Extract featured artists
        feat_patterns = [
            r'\(feat\. ([^)]+)\)',
            r'\(ft\. ([^)]+)\)',
            r'\(featuring ([^)]+)\)',
            r'feat\. ([^-]+)',
            r'ft\. ([^-]+)'
        ]
        
        for pattern in feat_patterns:
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match:
                featured_artists = match.group(1).strip()
                normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)
                break
        
        # Extract version info (Remix, Live, Acoustic, etc.)
        version_patterns = [
            r'\((.*?Remix)\)',
            r'\((.*?Mix)\)',
            r'\((Live.*?)\)',
            r'\((Acoustic.*?)\)',
            r'\((Demo.*?)\)',
            r'\((.*?Version)\)',
            r'\((.*?Edit)\)'
        ]
        
        for pattern in version_patterns:
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match:
                version_info = match.group(1).strip()
                normalized = normalized.replace(match.group(0), '')
                break
        
        # Clean up
        normalized = ' '.join(normalized.split())
        normalized = normalized.strip(' -–—.,;:()[]{}')
        
        return normalized, featured_artists, version_info
    
    @staticmethod
    def calculate_similarity(str1: str, str2: str, 
                           use_normalization: bool = True) -> float:
        """
        Calculate similarity score between two strings
        
        Args:
            str1: First string
            str2: Second string
            use_normalization: Whether to normalize before comparing
            
        Returns:
            Similarity score (0.0 to 1.0)
        """
        if not str1 or not str2:
            return 0.0
        
        if use_normalization:
            # Normalize both strings for comparison (without artist context since we're comparing)
            s1 = NormalizationService.normalize_album_title(str1).lower()
            s2 = NormalizationService.normalize_album_title(str2).lower()
        else:
            s1 = str1.lower()
            s2 = str2.lower()
        
        # Direct comparison
        if s1 == s2:
            return 1.0
        
        # Check substring matching
        if s1 in s2 or s2 in s1:
            # Boost score for substring matches
            return max(0.9, SequenceMatcher(None, s1, s2).ratio())
        
        # Use SequenceMatcher for fuzzy matching
        return SequenceMatcher(None, s1, s2).ratio()
    
    @staticmethod
    def create_normalized_metadata(result_dict: Dict) -> Dict:
        """
        Create normalized metadata object for a Result
        
        Args:
            result_dict: Dictionary representation of a Result object
            
        Returns:
            Normalized metadata dictionary to be added to Result
        """
        normalized_metadata = {
            'normalized_at': datetime.utcnow().isoformat(),
            'original_title': result_dict.get('title', ''),
            'original_artist': result_dict.get('artist', ''),
            'normalized_title': None,
            'normalized_artist': None,
            'normalized_channel': None,
            'extracted_features': None,
            'extracted_version': None
        }
        
        # Normalize album/playlist title with artist context for smart deduplication
        if result_dict.get('title'):
            normalized_metadata['normalized_title'] = NormalizationService.normalize_album_title(
                result_dict['title'],
                result_dict.get('artist', '')
            )
        
        # Normalize artist name
        if result_dict.get('artist'):
            normalized_metadata['normalized_artist'] = NormalizationService.normalize_artist_name(
                result_dict['artist']
            )
        
        # Normalize channel name (often same as artist but not always)
        if result_dict.get('channel'):
            normalized_metadata['normalized_channel'] = NormalizationService.normalize_artist_name(
                result_dict['channel']
            )
        
        # For future: track normalization would go here
        # if track_count == 1, we might normalize as a track title
        
        return normalized_metadata
    
    @staticmethod
    def merge_metadata(existing_metadata: Optional[Dict], 
                      new_metadata: Dict) -> Dict:
        """
        Merge normalized metadata, preserving existing enrichment
        
        Args:
            existing_metadata: Existing metadata (may include MusicBrainz data)
            new_metadata: New normalized metadata
            
        Returns:
            Merged metadata dictionary
        """
        if not existing_metadata:
            return new_metadata
        
        # Start with existing metadata
        merged = copy.deepcopy(existing_metadata)
        
        # Only update normalization fields, preserve enrichment data
        normalization_fields = [
            'normalized_at', 'original_title', 'original_artist',
            'normalized_title', 'normalized_artist', 'normalized_channel',
            'extracted_features', 'extracted_version'
        ]
        
        for field in normalization_fields:
            if field in new_metadata:
                merged[field] = new_metadata[field]
        
        return merged