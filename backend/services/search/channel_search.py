#!/usr/bin/env python3
"""
YouTube Music Downloader - Channel Search Service
Stateless search service that finds artist channels and their albums/playlists
"""

import asyncio
import subprocess
import json
import re
from typing import List, Optional, Dict, Any
from datetime import datetime

from backend.core.models import Result
from backend.services.youtube_deduplicator import YouTubeDeduplicator


class ChannelSearchService:
    """
    Stateless search service for finding artist channels and their content
    Uses yt-dlp to search for channels and extract album/playlist information
    """
    
    def __init__(self):
        self.service_name = "channel_search"
    
    async def search(self, artist: str, album: Optional[str] = None, cookie_options: Optional[Dict[str, Any]] = None, ytdlp_executor: Optional[callable] = None) -> List[Result]:
        """
        Search for artist channels and extract album content
        
        Args:
            artist: Artist name to search for
            album: Optional specific album name
            cookie_options: Cookie options from StateManager (single source of truth)
            ytdlp_executor: Rate-limited yt-dlp executor function from StateManager
            
        Returns:
            List of Result objects (stateless - doesn't modify state)
        """
        print(f"ChannelSearchService: Searching for artist '{artist}'" + 
              (f" album '{album}'" if album else ""))
        
        results = []
        
        # Use empty dict if no cookie options provided
        if cookie_options is None:
            cookie_options = {}
        
        try:
            # Step 1: Find artist channels (limit to top channels only)
            channels = await self._find_artist_channels(artist, cookie_options, ytdlp_executor)
            print(f"ChannelSearchService: Found {len(channels)} channels")
            
            # Step 2: Extract content from channels (limit extraction scope)
            for channel in channels[:3]:  # Limit to top 3 most relevant channels
                channel_results = await self._extract_channel_content(channel, artist, album, cookie_options, ytdlp_executor)
                results.extend(channel_results)
            
            print(f"ChannelSearchService: Found {len(results)} total results (optimized strategy)")
            
        except Exception as e:
            print(f"ChannelSearchService: Search failed: {e}")
        
        return results
    
    async def _find_artist_channels(self, artist: str, cookie_options: Dict[str, Any], ytdlp_executor: Optional[callable] = None) -> List[Dict[str, str]]:
        """
        Find YouTube channels for the artist
        
        Returns:
            List of channel info dicts with 'url', 'title', 'id'
        """
        channels = []
        
        try:
            # Search for channels using yt-dlp
            search_query = f"{artist} channel"
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--print", "%(uploader)s|%(channel_id)s|%(channel_url)s",
                "--default-search", "ytsearch10:",
                search_query
            ]
            
            # Add cookie options
            if "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            # Run command with rate limiting
            if ytdlp_executor:
                stdout, stderr, returncode = await ytdlp_executor(cmd)
            else:
                print("ChannelSearchService: Warning - no ytdlp_executor provided, skipping search")
                return []
            
            if returncode == 0 and stdout:
                lines = stdout.strip().split('\n')
                seen_channels = set()
                
                for line in lines:
                    if '|' in line:
                        parts = line.split('|')
                        if len(parts) >= 3:
                            uploader, channel_id, channel_url = parts[0], parts[1], parts[2]
                            
                            # Skip if we've already seen this channel
                            if channel_id in seen_channels:
                                continue
                            seen_channels.add(channel_id)
                            
                            # Filter for likely artist channels
                            if self._is_likely_artist_channel(uploader, artist):
                                channels.append({
                                    "url": channel_url,
                                    "title": uploader,
                                    "id": channel_id
                                })
            
        except Exception as e:
            print(f"ChannelSearchService: Channel search failed: {e}")
        
        return channels
    
    async def _extract_channel_content(self, channel: Dict[str, str], 
                                     artist: str, album: Optional[str] = None, 
                                     cookie_options: Optional[Dict[str, Any]] = None, 
                                     ytdlp_executor: Optional[callable] = None) -> List[Result]:
        """
        Extract album/playlist content from a channel
        
        Args:
            channel: Channel info dict
            artist: Artist name
            album: Optional specific album filter
            
        Returns:
            List of Result objects
        """
        results = []
        
        try:
            # Get channel playlists and albums
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                f"{channel['url']}/playlists"
            ]
            
            # Add cookie options
            if "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            # Run command with rate limiting
            if ytdlp_executor:
                stdout, stderr, returncode = await ytdlp_executor(cmd)
            else:
                print("ChannelSearchService: Warning - no ytdlp_executor provided, skipping search")
                return []
            
            if returncode == 0 and stdout:
                lines = stdout.strip().split('\n')
                
                for line in lines:
                    try:
                        playlist_data = json.loads(line)
                        
                        if self._is_album_playlist(playlist_data, album):
                            # Return the playlist itself as a result (not individual tracks)
                            # Extract thumbnail URL (prefer best quality)
                            thumbnail_url = None
                            if 'thumbnail' in playlist_data:
                                thumbnail_url = playlist_data['thumbnail']
                            elif 'thumbnails' in playlist_data and playlist_data['thumbnails']:
                                thumbnail_url = playlist_data['thumbnails'][-1].get('url')  # Last is usually best quality

                            # Extract track count (will be fixed centrally by StateManager if needed)
                            track_count = playlist_data.get('playlist_count', 1)

                            result = YouTubeDeduplicator.prepare_result(
                                url=playlist_data.get('webpage_url', ''),
                                title=playlist_data.get('title', ''),
                                artist=artist,
                                channel=channel['title'],
                                discovered_by=self.service_name,
                                thumbnail_url=thumbnail_url,
                                track_count=track_count
                            )
                            
                            if result:
                                # Higher quality score for channel-discovered playlists
                                result.quality_score = 0.9
                                results.append(result)
                            
                    except json.JSONDecodeError:
                        continue
            
        except Exception as e:
            print(f"ChannelSearchService: Channel content extraction failed: {e}")
        
        return results
    
    async def _extract_playlist_tracks(self, playlist_data: Dict[str, Any], 
                                     artist: str, channel: str, cookie_options: Optional[Dict[str, Any]] = None) -> List[Result]:
        """
        Extract individual track results from a playlist
        """
        results = []
        
        try:
            playlist_url = playlist_data.get('webpage_url') or playlist_data.get('url')
            if not playlist_url:
                return results
            
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                playlist_url
            ]
            
            # Add cookie options
            if "cookiesfrombrowser" in cookie_options:
                browser_info = cookie_options["cookiesfrombrowser"]
                cmd.extend(["--cookies-from-browser", browser_info[0]])
            
            # Run command with rate limiting
            if ytdlp_executor:
                stdout, stderr, returncode = await ytdlp_executor(cmd)
            else:
                print("ChannelSearchService: Warning - no ytdlp_executor provided, skipping detailed extraction")
                return results
            
            if returncode == 0 and stdout:
                lines = stdout.strip().split('\n')
                
                for line in lines:
                    try:
                        track_data = json.loads(line)
                        
                        # Create Result object
                        # Extract thumbnail URL (prefer best quality)
                        thumbnail_url = None
                        if 'thumbnail' in track_data:
                            thumbnail_url = track_data['thumbnail']
                        elif 'thumbnails' in track_data and track_data['thumbnails']:
                            thumbnail_url = track_data['thumbnails'][-1].get('url')  # Last is usually best quality

                        # Extract track count (will be fixed centrally by StateManager if needed)  
                        track_count = track_data.get('playlist_count', 1)

                        result = YouTubeDeduplicator.prepare_result(
                            url=track_data.get('webpage_url', ''),
                            title=track_data.get('title', ''),
                            artist=artist,
                            channel=channel,
                            discovered_by=self.service_name,
                            thumbnail_url=thumbnail_url,
                            track_count=track_count
                        )
                        
                        if result:
                            results.append(result)
                            
                    except json.JSONDecodeError:
                        continue
        
        except Exception as e:
            print(f"ChannelSearchService: Playlist track extraction failed: {e}")
        
        return results
    
    
    def _is_likely_artist_channel(self, channel_name: str, artist: str) -> bool:
        """
        Check if a channel name likely belongs to the artist
        """
        channel_lower = channel_name.lower()
        artist_lower = artist.lower()
        
        # Direct match
        if artist_lower in channel_lower:
            return True
        
        # Common patterns
        if (channel_lower.endswith("vevo") and 
            any(word in channel_lower for word in artist_lower.split())):
            return True
        
        if (channel_lower.startswith("official") and 
            artist_lower in channel_lower):
            return True
        
        return False
    
    def _is_album_playlist(self, playlist_data: Dict[str, Any], album_filter: Optional[str] = None) -> bool:
        """
        Check if a playlist represents an album
        """
        title = playlist_data.get('title', '').lower()
        
        # Skip obvious non-album playlists
        skip_keywords = ['live', 'concert', 'tour', 'greatest hits', 'best of', 'compilation']
        if any(keyword in title for keyword in skip_keywords):
            return False
        
        # If album filter specified, check for match
        if album_filter:
            return album_filter.lower() in title
        
        # Accept playlists that look like albums
        album_keywords = ['album', 'ep', 'single', 'deluxe', 'edition']
        return any(keyword in title for keyword in album_keywords) or len(title.split()) <= 4
    
    def get_service_info(self) -> Dict[str, Any]:
        """Get service information and status"""
        return {
            "name": self.service_name,
            "description": "Searches artist channels for albums and playlists",
            "cookie_support": bool(cookie_options),
            "status": "ready"
        }