#!/usr/bin/env python3
"""
YouTube Music Downloader - State Manager
THE ONLY component that modifies AppState. Single source of truth coordinator.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from fastapi import WebSocket

from .models import AppState, AppStatus, Result, SearchStatus, CurrentState
from backend.services.youtube_deduplicator import YouTubeDeduplicator
from backend.services.search.channel_search import ChannelSearchService
from backend.services.search.youtube_music_search import YouTubeMusicSearchService
from backend.services.search.playlist_search import PlaylistSearchService
from backend.services.search.google_search import GoogleSearchService
from backend.services.search.direct_album_search import DirectAlbumSearchService
from backend.services.search.genre_context_search import GenreContextSearchService
from backend.services.search.alternative_title_search import AlternativeTitleSearchService
from backend.services.cookie_service import CookieService
from backend.services.availability_verification_service import AvailabilityVerificationService, VerificationCache
from backend.services.normalization_service import NormalizationService
from backend.services.musicbrainz_service import MusicBrainzService, MetadataCache
from backend.services.ytdlp_rate_limiter import YTDLPRateLimiter


class StateManager:
    """
    THE GOD COMPONENT - Only this class modifies AppState
    Coordinates all services and sends updates to frontend
    """
    
    def __init__(self):
        self.state = AppState()
        self.websockets: List[WebSocket] = []
        self.search_tasks: List[asyncio.Task] = []
        
        # Track YouTube IDs already counted as duplicates (prevent double-counting)
        self.duplicate_counted_ids: set = set()
        
        
        # Detect and store cookie information
        self.state.config.cookie_info = CookieService.detect_and_create_cookie_info()
        
        # Initialize search services
        self.search_services = {
            "channel_search": ChannelSearchService(),
            "youtube_music_search": YouTubeMusicSearchService(),
            "playlist_search": PlaylistSearchService(),
            "google_search": GoogleSearchService(),
            "direct_album_search": DirectAlbumSearchService(),
            "genre_context_search": GenreContextSearchService(),
            "alternative_title_search": AlternativeTitleSearchService()
        }
        
        # Initialize availability verification service with cache
        self.availability_service = AvailabilityVerificationService()
        self.verification_cache = VerificationCache(cache_duration_minutes=30)
        
        # Initialize metadata services and cache
        self.normalization_service = NormalizationService()
        self.musicbrainz_service = MusicBrainzService()
        self.metadata_cache = MetadataCache(cache_duration_hours=24)
        
        # Initialize yt-dlp rate limiter (0.5s delay = 2 requests/second)
        self.ytdlp_rate_limiter = YTDLPRateLimiter(delay=0.5)
        
        # Track async tasks for race condition handling
        self.normalization_tasks: Dict[str, asyncio.Task] = {}  # youtube_id -> task
        self.metadata_tasks: Dict[str, asyncio.Task] = {}  # youtube_id -> task
        
        
    async def add_websocket(self, websocket: WebSocket):
        """Add WebSocket connection"""
        self.websockets.append(websocket)
        # Send current state immediately
        await self.send_state()
    
    async def remove_websocket(self, websocket: WebSocket):
        """Remove WebSocket connection"""
        if websocket in self.websockets:
            self.websockets.remove(websocket)
    
    async def send_state(self):
        """Send complete AppState to all connected clients"""
        print(f"DEBUG: send_state called, {len(self.websockets)} websockets connected")
        if not self.websockets:
            return
        
        # Convert to dict with JSON-compatible datetime serialization
        state_dict = self.state.model_dump(mode='json')
        
        # Check actual data being sent
        results = state_dict.get("results", [])
        if results:
            sample_result = results[-1]  # Last result
            print(f"DEBUG: Sample result JSON keys: {list(sample_result.keys())}")
            print(f"DEBUG: normalized field: {sample_result.get('normalized', 'MISSING')}")
            print(f"DEBUG: metadata field: {sample_result.get('metadata', 'MISSING')}")
        
        message = {
            "type": "state_update",
            "data": state_dict,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Send to all connected WebSockets
        disconnected = []
        for websocket in self.websockets:
            try:
                await websocket.send_text(json.dumps(message))
            except Exception as e:
                print(f"WebSocket send failed: {e}")
                disconnected.append(websocket)
        
        # Remove disconnected WebSockets
        for ws in disconnected:
            self.websockets.remove(ws)
    
    async def start_search(self, query: str):
        """Start search process - creates fresh AppState and coordinates all services"""
        # Prevent duplicate search requests
        if self.state.current.status == AppStatus.SEARCHING:
            print(f"StateManager: Search already in progress, ignoring duplicate request for '{query}'")
            return
            
        print(f"StateManager: Starting search for '{query}'")
        
        # Create fresh AppState object (resets ALL counters and tracking)
        old_config = self.state.config  # Preserve config
        self.state = AppState()
        self.state.config = old_config  # Restore config
        
        # Reset duplicate tracking
        self.duplicate_counted_ids.clear()
        
        # Clear all caches at search start (fresh search, no cached data)
        self.verification_cache.cache.clear()
        if hasattr(self, 'normalization_service') and hasattr(self.normalization_service, 'cache'):
            self.normalization_service.cache.clear()
        if hasattr(self, 'musicbrainz_service') and hasattr(self.musicbrainz_service, 'cache'):
            self.musicbrainz_service.cache.clear()
        
        # Set search parameters
        self.state.job.query = query
        self.state.job.started_at = datetime.utcnow()
        self.state.job.strategies_completed = []
        self.state.current.status = AppStatus.SEARCHING
        # Use enabled strategies from config
        strategies = self.state.config.enabled_strategies
        self.state.totals.strategies_total = len(strategies)
        self.state.update_timestamp()
        
        # Cancel any existing search tasks
        for task in self.search_tasks:
            if not task.done():
                task.cancel()
        self.search_tasks = []
        
        # Set initial activity
        await self._update_activity(f"Starting search for '{query}'...")
        
        # Start all search services in parallel
        await self._start_search_services(strategies)
    
    async def _start_search_services(self, strategies: List[str]):
        """Start all requested search services in parallel"""
        search_tasks = []
        
        for strategy in strategies:
            if strategy in self.search_services:
                task = asyncio.create_task(
                    self._run_search_service(strategy, self.state.job.query)
                )
                search_tasks.append(task)
                print(f"StateManager: Started search service: {strategy}")
            else:
                print(f"StateManager: Unknown search strategy: {strategy}")
        
        self.search_tasks = search_tasks
    
    async def _run_search_service(self, strategy_name: str, query: str):
        """Run a single search service and process its results"""
        try:
            # Start strategy tracking
            await self._start_strategy(strategy_name)
            
            service = self.search_services[strategy_name]
            
            # Get cookie options from AppState (single source of truth)
            cookie_options = {}
            if self.state.config.cookie_info.yt_dlp_compatible and self.state.config.cookie_info.recommended_browser:
                cookie_options["cookiesfrombrowser"] = (self.state.config.cookie_info.recommended_browser, None, None, None)
            
            results = await service.search(query, cookie_options=cookie_options, ytdlp_executor=self.execute_ytdlp_command)
            
            print(f"StateManager: {strategy_name} returned {len(results)} results")
            
            # Process each result through deduplication AND verification
            added_count = 0
            for result in results:
                dedup_result = YouTubeDeduplicator.should_add_result(result, self.state.results)
                
                if dedup_result == "merge":
                    # Better quality duplicate found - merge metadata with existing
                    existing = YouTubeDeduplicator.find_duplicate(result, self.state.results)
                    if existing:
                        # Merge better metadata into existing result (no availability check needed)
                        merged_result = self._merge_metadata(existing, result)
                        
                        # Update the existing result in place
                        existing_index = self.state.results.index(existing)
                        self.state.results[existing_index] = merged_result
                        print(f"StateManager: Merged better metadata for {result.youtube_id}")
                        
                        # Track the merge (but don't increment found count)
                        await self._update_activity(f"Enhanced {merged_result.artist} - {merged_result.title}")
                        
                        # Update state and continue normalization if needed
                        self.state.update_timestamp()
                        await self.send_state()
                        await self._trigger_normalization_if_needed(merged_result)
                
                elif dedup_result == True:
                    # Unique result - add normally
                    is_available = await self._check_availability_before_adding(result, cookie_options)
                    
                    if is_available:
                        success = self.state.add_result(result)
                        if success:
                            added_count += 1
                            # Update results count for frontend responsiveness
                            self._update_results_count()
                            # Track item as successfully found and added to AppState
                            self.state.totals.found += 1
                            
                            print(f"StateManager: Found counter incremented to {self.state.totals.found} for {result.youtube_id}")
                            # Trigger normalization for albums/playlists
                            await self._trigger_normalization_if_needed(result)
                        else:
                            # AppState.add_result() returned False - race condition duplicate
                            # Removed counting logic
                            pass
                    else:
                        # Removed counting logic
                        pass
                
                else:  # dedup_result == False
                    # Skip - existing duplicate is better (never added to AppState, so no duplicate counter)
                    pass
            
            print(f"StateManager: {strategy_name} added {added_count}/{len(results)} unique results")
            await self._complete_strategy(strategy_name, added_count)
            
            # Check if all strategies are complete and do first cleanup
            if len(self.state.job.strategies_completed) >= self.state.totals.strategies_total:
                print(f"StateManager: All search strategies completed - running single track cleanup")
                await self._remove_single_tracks()
            
        except Exception as e:
            print(f"StateManager: Search service {strategy_name} failed: {e}")
            # Removed counting logic
            await self.search_strategy_completed(strategy_name, 0)

    async def add_result(self, result: Result) -> bool:
        """
        Add new result ONLY after YouTube ID deduplication
        Returns True if added, False if duplicate
        """
        # Check for YouTube ID duplicate using deduplicator
        if not YouTubeDeduplicator.should_add_result(result, self.state.results):
            return False  # Never added to AppState, so no duplicate counter
        
        # Add result
        success = self.state.add_result(result)
        if success:
            self._update_results_count()
            print(f"StateManager: Added result {result.id} - {result.title}")
            await self.send_state()
        
        return success
    
    async def update_result(self, result_id: str, **updates) -> bool:
        """Update result with new data (e.g., verification results)"""
        success = self.state.update_result(result_id, **updates)
        if success:
            # Update statistics based on verification status
            if 'verified' in updates:
                # Removed verification stats
                pass
            
            print(f"StateManager: Updated result {result_id} with {list(updates.keys())}")
            await self.send_state()
        
        return success
    
    async def remove_result(self, result_id: str, reason: str = "") -> bool:
        """Remove result (e.g., failed validation, low quality)"""
        success = self.state.remove_result(result_id)
        if success:
            self._update_results_count()
            print(f"StateManager: Removed result {result_id} - {reason}")
            await self.send_state()
        
        return success
    
    async def validate_result(self, result_id: str) -> bool:
        """Validate result using Quality Manager (URL accessibility, etc.)"""
        # TODO: Use Quality Manager service to validate
        print(f"StateManager: Would validate result {result_id}")
        return True
    
    async def search_strategy_completed(self, strategy_name: str, results_count: int):
        """Mark search strategy as completed"""
        if strategy_name not in self.state.job.strategies_completed:
            self.state.job.strategies_completed.append(strategy_name)
            print(f"StateManager: Strategy '{strategy_name}' completed with {results_count} results")
        
        # Check if all strategies completed
        if len(self.state.job.strategies_completed) >= self.state.totals.strategies_total:
            await self.search_completed()
        
        await self.send_state()
    
    async def _check_availability_before_adding(self, result: Result, cookie_options: Dict) -> bool:
        """
        Check if result is available before adding to AppState
        Uses cache for efficiency
        Does NOT modify result.verified (that's for metadata verification)
        """
        # Check cache first
        cached = self.verification_cache.get(result.youtube_url)
        
        if cached is not None:
            is_available, error_msg = cached
            return is_available
        
        # Not in cache, verify now
        is_available, error_msg = await self.availability_service.verify_availability(
            result.youtube_url,
            cookie_options
        )
        
        # Cache the result
        self.verification_cache.set(result.youtube_url, is_available, error_msg)
        
        return is_available
    
    async def search_completed(self):
        """All search strategies completed"""
        self.state.current.status = AppStatus.IDLE  # No need for separate verification phase
        print(f"StateManager: Search completed. Found {self.state.totals.found} results")
        
        # Search completed - all strategies finished
        
        # Filter out single tracks (keep only multi-track albums/playlists)
        initial_count = len(self.state.results)
        single_tracks = [r for r in self.state.results if r.track_count is not None and r.track_count <= 1]
        multi_track_results = [r for r in self.state.results if r.track_count is None or r.track_count > 1]
        single_track_count = len(single_tracks)
        
        if single_track_count > 0:
            # Track each single track removal as a duplicate
            for single_track in single_tracks:
                # Removed counting logic
                pass
            
            self.state.results = multi_track_results
            print(f"StateManager: Filtered out {single_track_count} single tracks, "
                  f"kept {len(multi_track_results)} multi-track results")
        
        # All results are already verified during addition
        # Removed statistics
        
        # Set final status and activity
        self.state.current.status = AppStatus.IDLE
        self.state.current.active_strategies.clear()
        
        total_items = len(self.state.results)
        
        if total_items == 0:
            await self._update_activity("Search completed - no results found")
            await self._update_activity(f"Search completed - found {total_items} items")
    
    async def verify_all_results(self):
        """Verify availability of all results using cache and batch verification"""
        print(f"StateManager: Starting verification of {len(self.state.results)} results")
        
        # Get cookie options for verification
        cookie_options = {}
        if self.state.config.cookie_info.yt_dlp_compatible and self.state.config.cookie_info.recommended_browser:
            cookie_options["cookiesfrombrowser"] = (self.state.config.cookie_info.recommended_browser, None, None, None)
        
        # Separate results that need verification
        to_verify = []
        verified_from_cache = 0
        
        for result in self.state.results:
            # Check cache first
            cached = self.verification_cache.get(result.youtube_url)
            
            if cached is not None:
                is_available, error_msg = cached
                if is_available:
                    pass  # Result is available - no action needed
                else:
                    pass  # Result unavailable - cache tracks this
                verified_from_cache += 1
            else:
                # Needs verification
                to_verify.append(result)
        
        print(f"StateManager: {verified_from_cache} results verified from cache, "
              f"{len(to_verify)} need verification")
        
        if to_verify:
            # Batch verify remaining results
            verification_results = await self.availability_service.verify_batch(
                to_verify, 
                cookie_options,
                max_concurrent=10  # Verify up to 10 URLs concurrently
            )
            
            # Update results and cache
            for result in to_verify:
                if result.id in verification_results:
                    is_available, error_msg = verification_results[result.id]
                    
                    # Availability tracked in cache only
                    if is_available:
                        pass  # Result is available
                    else:
                        pass  # Result unavailable
                    
                    # Update cache
                    self.verification_cache.set(result.youtube_url, is_available, error_msg)
                else:
                    # Verification failed - tracked in cache
                    pass
        
        # Update statistics
        # Removed verification stats
        
        # Set status back to idle
        self.state.current.status = AppStatus.IDLE
        
        # Log final stats
        # Removed statistics
        
        # Send final state update
        await self.send_state()
    
    async def verify_single_result(self, result_id: str):
        """Verify a single result by ID"""
        result = self.state.get_result_by_id(result_id)
        if not result:
            return
        
        # Check cache first
        cached = self.verification_cache.get(result.youtube_url)
        
        if cached is not None:
            is_available, error_msg = cached
            print(f"StateManager: Using cached verification for {result.title}")
        else:
            # Get cookie options
            cookie_options = {}
            if self.state.config.cookie_info.yt_dlp_compatible and self.state.config.cookie_info.recommended_browser:
                cookie_options["cookiesfrombrowser"] = (self.state.config.cookie_info.recommended_browser, None, None, None)
            
            # Verify URL
            is_available, error_msg = await self.availability_service.verify_availability(
                result.youtube_url,
                cookie_options
            )
            
            # Update cache
            self.verification_cache.set(result.youtube_url, is_available, error_msg)
        
        # Update result
        if is_available:
            pass  # Available - tracked in cache
            result.error_message = None
        else:
            pass  # Unavailable - tracked in cache
            result.error_message = error_msg
        
        # Update statistics and send state
        # Removed verification stats
        await self.send_state()
    
    def _update_verification_stats(self):
        """Update verification statistics"""
        # Statistics are calculated dynamically from results
        # No need to maintain separate counters
        pass
    
    async def get_state(self) -> AppState:
        """Get current state (read-only access)"""
        return self.state
    
    async def clear_state(self):
        """Reset to initial state"""
        self.state = AppState()
        await self.send_state()
    
    def get_result_count(self) -> int:
        """Get current result count"""
        return len(self.state.results)
    
    def get_search_progress(self) -> Dict[str, Any]:
        """Get search progress information"""
        completed = len(self.state.job.strategies_completed)
        total = self.state.totals.strategies_total
        
        return {
            "completed_strategies": completed,
            "total_strategies": total,
            "progress_percent": (completed / total * 100) if total > 0 else 0,
            "current_status": self.state.current.status,
# Removed results_found counter
        }
    
    async def _trigger_normalization_if_needed(self, result: Result):
        """
        Trigger normalization for albums/playlists (track_count > 1)
        Handles race conditions and replacement scenarios
        """
        # Only normalize albums/playlists, not single tracks
        track_count = result.track_count or 0
        if track_count <= 1:
            return
        
        # Check if already has normalization data
        if result.normalized and 'title' in result.normalized:
            print(f"StateManager: Result {result.youtube_id} already has normalization data")
            return
        
        # Cancel any existing normalization task for this YouTube ID
        if result.youtube_id in self.normalization_tasks:
            existing_task = self.normalization_tasks[result.youtube_id]
            if not existing_task.done():
                existing_task.cancel()
                print(f"StateManager: Cancelled previous normalization for {result.youtube_id}")
        
        # Start normalization task
        task = asyncio.create_task(self._normalize_result(result))
        self.normalization_tasks[result.youtube_id] = task
    
    async def _normalize_result(self, result: Result):
        """
        Normalize a result asynchronously
        Handles race conditions where result might be replaced during normalization
        """
        try:
            youtube_id = result.youtube_id
            print(f"StateManager: Starting normalization for {youtube_id} - {result.title}")
            
            # Create normalized metadata
            result_dict = result.model_dump()
            normalized_metadata = NormalizationService.create_normalized_metadata(result_dict)
            
            # Small delay to simulate processing (in real case, MusicBrainz lookup would go here)
            await asyncio.sleep(0.1)
            
            # Find current result by YouTube ID (might have been replaced)
            current_result = self.state.get_result_by_youtube_id(youtube_id)
            
            if not current_result:
                print(f"StateManager: Result {youtube_id} was removed during normalization")
                return
            
            # Merge with any existing metadata
            if current_result.normalized:
                # Convert current normalized data to old format for merge compatibility
                current_old_format = {
                    'normalized_title': current_result.normalized.get('title', ''),
                    'normalized_artist': current_result.normalized.get('artist', ''),
                    'normalized_album': current_result.normalized.get('album', '')
                }
                normalized_metadata = NormalizationService.merge_metadata(
                    current_old_format,
                    normalized_metadata
                )
            
            # Update the current result with normalized metadata in new format
            current_result.normalized = {
                'title': normalized_metadata.get('normalized_title', ''),
                'artist': normalized_metadata.get('normalized_artist', ''),
                'album': normalized_metadata.get('normalized_album', '')
            }
            self.state.update_timestamp()
            
            print(f"StateManager: Normalized {youtube_id} - '{normalized_metadata.get('normalized_title')}'")
            
            # Track normalization completion
            
            # Check for normalized content duplicates before metadata retrieval
            duplicate_result = self._find_normalized_duplicate(current_result)
            if duplicate_result:
                print(f"StateManager: Found normalized duplicate - {normalized_metadata.get('normalized_artist')} / {normalized_metadata.get('normalized_album')}")
                
                # Compare content quality to decide which to keep
                if self._is_better_content_quality(current_result, duplicate_result):
                    print(f"StateManager: New result has better content quality - removing existing duplicate")
                    # Transfer any existing metadata from duplicate to current result
                    if duplicate_result.normalized:
                        # Keep current normalized data (better quality)
                        pass
                    if duplicate_result.metadata:
                        # Transfer metadata from duplicate
                        current_result.metadata = duplicate_result.metadata.copy()
                    
                    # Remove the lower quality duplicate from state
                    self._remove_result_from_state(duplicate_result, "after_normalization")
                    # Track duplicate removal
                    # Removed counting logic
                    
                    # Cancel any active metadata tasks for the duplicate
                    if duplicate_result.youtube_id in self.metadata_tasks:
                        self.metadata_tasks[duplicate_result.youtube_id].cancel()
                        del self.metadata_tasks[duplicate_result.youtube_id]
                        print(f"StateManager: Cancelled metadata task for removed duplicate {duplicate_result.youtube_id}")
                else:
                    print(f"StateManager: Existing result has better content quality - removing current")
                    # Transfer normalization metadata to existing result if it doesn't have it
                    if not duplicate_result.normalized:
                        duplicate_result.normalized = current_result.normalized.copy()
                        self.state.update_timestamp()
                    
                    # Remove current result from state (lower quality)
                    self._remove_result_from_state(current_result, "after_normalization")
                    # Track duplicate removal  
                    # Removed counting logic
                    
                    # Cancel any active normalization task for current result
                    if current_result.youtube_id in self.normalization_tasks:
                        del self.normalization_tasks[current_result.youtube_id]
                    
                    # Don't trigger metadata retrieval for removed result
                    return
                
                # Send state update after deduplication
                await self.send_state()
            
            # Trigger metadata retrieval after successful normalization (and deduplication)
            await self._trigger_metadata_retrieval_if_needed(current_result)
            
        except asyncio.CancelledError:
            print(f"StateManager: Normalization cancelled for {result.youtube_id}")
        except Exception as e:
            print(f"StateManager: Normalization failed for {result.youtube_id}: {e}")
        finally:
            # Clean up task tracking
            if result.youtube_id in self.normalization_tasks:
                del self.normalization_tasks[result.youtube_id]
    
    # ====== METADATA MERGING METHODS ======
    
    def _merge_metadata(self, existing_result: Result, new_result: Result) -> Result:
        """
        Merge better metadata from new_result into existing_result
        Preserves normalization/verification metadata from existing result
        """
        # Start with existing result to preserve verification metadata
        merged = existing_result.model_copy(deep=True)
        
        # Update with better raw metadata from new result
        if new_result.track_count and (not existing_result.track_count or new_result.track_count > existing_result.track_count):
            merged.track_count = new_result.track_count
            
        if new_result.thumbnail_url and not existing_result.thumbnail_url:
            merged.thumbnail_url = new_result.thumbnail_url
            
        if new_result.quality_score > existing_result.quality_score:
            merged.quality_score = new_result.quality_score
            
        # Update discovered_by to reflect multiple sources
        if new_result.discovered_by and new_result.discovered_by not in merged.discovered_by:
            if merged.discovered_by:
                merged.discovered_by += f", {new_result.discovered_by}"
            else:
                merged.discovered_by = new_result.discovered_by
        
        print(f"StateManager: Merged metadata - track_count: {merged.track_count}, thumbnail: {'yes' if merged.thumbnail_url else 'no'}, sources: {merged.discovered_by}")
        
        return merged
    
    # ====== ACTIVITY TRACKING METHODS ======
    
    async def _update_activity(self, message: str):
        """Update current activity and send state"""
        self.state.current.update_activity(message)
        await self.send_state()
    
    def _update_results_count(self):
        """Update results count - simple count of current results"""
        self.state.totals.results = len(self.state.results)
    
    # ====== COUNTING LOGIC REMOVED =======
    # Only strategy progress and simple results count tracked in totals now
    
    async def _start_strategy(self, strategy_name: str):
        """Mark strategy as starting"""
        if strategy_name not in self.state.current.active_strategies:
            self.state.current.active_strategies.append(strategy_name)
        # Activity tracked via status and last_activity
        
        strategy_display = strategy_name.replace('_', ' ').title()
        await self._update_activity(f"Searching with {strategy_display}...")
    
    async def _complete_strategy(self, strategy_name: str, results_count: int):
        """Mark strategy as completed"""
        self.state.totals.strategies_completed += 1
        if strategy_name in self.state.current.active_strategies:
            self.state.current.active_strategies.remove(strategy_name)
        
        strategy_display = strategy_name.replace('_', ' ').title()
        await self._update_activity(f"Completed {strategy_display} - found {results_count} items")
        
        # Trigger completion check
        await self.search_strategy_completed(strategy_name, results_count)
    
    # Note: Activity tracking now handled via status, last_activity, and active_strategies
    
    # ====== METADATA RETRIEVAL METHODS ======
    
    async def _trigger_metadata_retrieval_if_needed(self, result: Result):
        """
        Trigger metadata retrieval for normalized results
        Only retrieves if normalized data exists and metadata not yet retrieved
        """
        # Check if normalization data exists
        if not result.normalized or 'title' not in result.normalized:
            return
        
        # Check if metadata already retrieved
        if result.metadata and 'id' in result.metadata:
            print(f"StateManager: Result {result.youtube_id} already has metadata")
            return
        
        # Extract normalized data for metadata query
        normalized_title = result.normalized.get('title')
        normalized_artist = result.normalized.get('artist', result.artist)
        
        if not normalized_title or not normalized_artist:
            print(f"StateManager: Missing normalized data for {result.youtube_id}")
            return
        
        # Parse "Artist - Album" format
        if " - " in normalized_title:
            artist_part, album_part = normalized_title.split(" - ", 1)
            # Use the artist from normalized title, fallback to normalized_artist
            query_artist = artist_part
            query_album = album_part
        else:
            # Single word title, might be self-titled or channel content
            query_artist = normalized_artist
            query_album = normalized_title
        
        # Cancel any existing metadata task for this YouTube ID
        if result.youtube_id in self.metadata_tasks:
            existing_task = self.metadata_tasks[result.youtube_id]
            if not existing_task.done():
                existing_task.cancel()
                print(f"StateManager: Cancelled previous metadata retrieval for {result.youtube_id}")
        
        # Start metadata retrieval task
        task = asyncio.create_task(self._retrieve_metadata(result, query_artist, query_album))
        self.metadata_tasks[result.youtube_id] = task
    
    async def _retrieve_metadata(self, result: Result, artist: str, album: str):
        """
        Retrieve metadata from MusicBrainz with caching and race condition handling
        """
        try:
            youtube_id = result.youtube_id
            print(f"StateManager: Starting metadata retrieval for {youtube_id} - {artist} / {album}")
            
            # Find current result first
            current_result = self.state.get_result_by_youtube_id(youtube_id)
            if not current_result:
                print(f"StateManager: Result {youtube_id} not found for metadata processing")
                return
            
            # Check cache first
            cached_metadata = self.metadata_cache.get("musicbrainz", artist, album)
            if cached_metadata is not None:
                metadata = cached_metadata
                print(f"StateManager: Using cached metadata for {artist} - {album}")
            else:
                # Retrieve from MusicBrainz
                # Verification status tracked via last_activity
                metadata = await self.musicbrainz_service.search_release(artist, album)
                
                # Cache the result (including None for "not found")
                self.metadata_cache.set("musicbrainz", artist, album, metadata)
                # Verification complete
            
            # Recheck current result by YouTube ID (might have been replaced during retrieval)
            current_result = self.state.get_result_by_youtube_id(youtube_id)
            
            if not current_result:
                print(f"StateManager: Result {youtube_id} was removed during metadata retrieval")
                return
            
            # Add normalization data AFTER re-fetching the result object
            normalized_title = self.normalization_service.normalize_album_title(current_result.title, current_result.artist)
            normalized_artist = self.normalization_service.normalize_artist_name(current_result.artist)
            
            # Populate structured normalization field
            if not current_result.normalized:
                current_result.normalized = {}
            current_result.normalized.update({
                "title": normalized_title,
                "artist": normalized_artist
            })
            
            # Send state update after normalization
            await self.send_state()
            
            # Store metadata in new format
            if metadata:
                current_result.metadata = metadata.copy()
                
                # ALSO populate structured metadata field
                if not current_result.metadata:
                    current_result.metadata = {}
                current_result.metadata.update(metadata)
                
                self.state.update_timestamp()
                # Removed verification stats  # Update the statistics after marking as verified
                
                print(f"StateManager: Added MusicBrainz metadata for {youtube_id} - ID: {metadata.get('id')}")
                
                # Send state update after metadata retrieval
                await self.send_state()
                
                # Track verification success
                
                # Check for MBID-based duplicates after successful metadata retrieval
                metadata_id = metadata.get('id')
                if metadata_id:
                    duplicate_results = self._find_metadata_id_duplicates(current_result, metadata_id)
                    if duplicate_results:
                        print(f"StateManager: Found {len(duplicate_results)} metadata ID duplicates for {metadata_id}")
                        
                        # Find the best quality result among all duplicates + current
                        all_results = [current_result] + duplicate_results
                        best_result = max(all_results, key=lambda r: self._get_content_priority(r))
                        
                        # If current result is not the best, find which one is
                        for candidate in all_results:
                            if self._is_better_content_quality(candidate, best_result):
                                best_result = candidate
                        
                        # Merge metadata from all duplicates into best result
                        other_results = [r for r in all_results if r != best_result]
                        best_result = self._merge_metadata_id_metadata(best_result, other_results)
                        
                        # Update state: keep best result, remove others
                        for result_to_remove in other_results:
                            # Remove lower quality result from state (including current result if it's not the best)
                            self._remove_result_from_state(result_to_remove, "after_musicbrainz")
                            # Track MBID-based duplicate removal
                            # Removed counting logic
                            
                            # Cancel any active metadata tasks for removed duplicates
                            if result_to_remove.youtube_id in self.metadata_tasks:
                                self.metadata_tasks[result_to_remove.youtube_id].cancel()
                                del self.metadata_tasks[result_to_remove.youtube_id]
                                print(f"StateManager: Cancelled metadata task for removed metadata ID duplicate {result_to_remove.youtube_id}")
                            
                            # If we're removing the current result, we need to stop processing it
                            if result_to_remove.youtube_id == current_result.youtube_id:
                                print(f"StateManager: Current result {current_result.youtube_id} removed - better metadata ID duplicate {best_result.youtube_id} kept")
                                return  # Don't continue processing removed result
                        
                        # Send state update after MBID deduplication
                        await self.send_state()
                        
                        # Final single track cleanup after MBID deduplication (last final deduplication)
                        print(f"StateManager: Running final single track cleanup after MBID deduplication")
                        await self._remove_single_tracks()
            else:
                # Mark as unverified (not found in MusicBrainz) in new format
                if not current_result.metadata:
                    current_result.metadata = {}
                current_result.metadata['mb_not_found'] = datetime.utcnow().isoformat()
                self.state.update_timestamp()
                # Removed verification stats  # Update the statistics after marking as unverified
                
                print(f"StateManager: No MusicBrainz match found for {youtube_id} - {artist} / {album}")
                
                # Track verification failure
                
        except asyncio.CancelledError:
            print(f"StateManager: Metadata retrieval cancelled for {result.youtube_id}")
        except Exception as e:
            print(f"StateManager: Metadata retrieval failed for {result.youtube_id}: {e}")
            # Removed counting logic
        finally:
            # Clean up task tracking
            if result.youtube_id in self.metadata_tasks:
                del self.metadata_tasks[result.youtube_id]
    
    async def execute_ytdlp_command(self, cmd: list, **kwargs) -> tuple:
        """
        Execute yt-dlp command with centralized rate limiting
        
        This is the ONLY method search services should use for yt-dlp calls.
        Ensures consistent rate limiting across all search strategies.
        
        Args:
            cmd: Command list for yt-dlp subprocess
            **kwargs: Additional arguments for subprocess execution
            
        Returns:
            (stdout, stderr, returncode) tuple
        """
        return await self.ytdlp_rate_limiter.execute_subprocess(cmd, **kwargs)
    
    def _get_content_priority(self, result: Result) -> int:
        """
        Get content priority score for deduplication quality comparison
        Higher score = better quality content type
        
        Returns:
            3: Playlist/Album (multiple tracks)
            2: Chaptered album (single video with chapters) 
            1: Single track
        """
        track_count = result.track_count or 0
        
        if track_count > 1:
            return 3  # Playlist/Album - highest priority
        
        # Check for chapter indicators in title/description
        title_lower = result.title.lower()
        if any(indicator in title_lower for indicator in ['chapter', 'full album', 'complete album']):
            return 2  # Chaptered album - medium priority
        
        return 1  # Single track - lowest priority
    
    def _is_better_content_quality(self, new_result: Result, existing_result: Result) -> bool:
        """
        Compare content quality between two results with same normalized content
        Returns True if new_result has better quality than existing_result
        
        Priority: Playlists > Chaptered albums > Single tracks
        """
        new_priority = self._get_content_priority(new_result)
        existing_priority = self._get_content_priority(existing_result)
        
        if new_priority != existing_priority:
            return new_priority > existing_priority
        
        # Same content type, use existing quality metrics
        # Use the same logic as YouTubeDeduplicator but without metadata consideration
        new_score = 0
        existing_score = 0
        
        # Track count comparison
        new_tracks = new_result.track_count or 0
        existing_tracks = existing_result.track_count or 0
        if new_tracks > existing_tracks:
            new_score += 3
        else:
            existing_score += 3
        
        # Thumbnail availability
        if new_result.thumbnail_url and not existing_result.thumbnail_url:
            new_score += 2
        else:
            existing_score += 2
        
        # Quality score comparison (discovery quality only)
        if new_result.quality_score > existing_result.quality_score:
            new_score += 1
        else:
            existing_score += 1
        
        return new_score > existing_score
    
    def _find_normalized_duplicate(self, target_result: Result) -> Optional[Result]:
        """
        Find existing result in state.results with same normalized content
        
        Args:
            target_result: Result with normalized metadata to check for duplicates
            
        Returns:
            Existing duplicate result or None if no duplicate found
        """
        if not target_result.normalized:
            return None
        
        target_artist = target_result.normalized.get('artist', '').lower()
        target_album = target_result.normalized.get('album', '').lower()
        
        if not target_artist or not target_album:
            return None
        
        # Search existing results for normalized content match
        for existing_result in self.state.results:
            if existing_result.youtube_id == target_result.youtube_id:
                continue  # Skip self
                
            if not existing_result.normalized:
                continue
                
            existing_artist = existing_result.normalized.get('artist', '').lower()
            existing_album = existing_result.normalized.get('album', '').lower()
            
            if existing_artist == target_artist and existing_album == target_album:
                return existing_result
        
        return None
    
    def _find_metadata_id_duplicates(self, target_result: Result, metadata_id: str) -> List[Result]:
        """
        Find existing results in state.results with same metadata ID
        
        Args:
            target_result: Result to exclude from search (usually the current result)
            metadata_id: Metadata ID to search for
            
        Returns:
            List of existing duplicate results (excluding target_result)
        """
        duplicates = []
        
        if not metadata_id:
            return duplicates
        
        for existing_result in self.state.results:
            if existing_result.youtube_id == target_result.youtube_id:
                continue  # Skip target result
                
            if not existing_result.metadata:
                continue
                
            existing_id = existing_result.metadata.get('id')
            if existing_id == metadata_id:
                duplicates.append(existing_result)
        
        return duplicates
    
    def _merge_metadata_id_metadata(self, best_result: Result, duplicate_results: List[Result]) -> Result:
        """
        Merge metadata from duplicate results into the best result
        
        Args:
            best_result: The result to keep (highest content quality)
            duplicate_results: List of duplicate results to merge from
            
        Returns:
            The best_result with merged metadata
        """
        if not best_result.metadata:
            best_result.metadata = {}
        
        # Merge metadata from all duplicates
        for duplicate in duplicate_results:
            if duplicate.metadata:
                # Preserve existing metadata, but don't overwrite better data
                for key, value in duplicate.metadata.items():
                    if key not in best_result.metadata or not best_result.metadata[key]:
                        best_result.metadata[key] = value
        
        print(f"StateManager: Merged MBID metadata from {len(duplicate_results)} duplicates into {best_result.youtube_id}")
        return best_result
    
    def _replace_result_in_state(self, old_result: Result, new_result: Result):
        """
        Replace an existing result in state.results with a new result in-place
        Preserves list position and triggers state update
        """
        try:
            # Find index of old result
            old_index = self.state.results.index(old_result)
            
            # Replace in-place
            self.state.results[old_index] = new_result
            
            # Update timestamp and notify frontend
            self.state.update_timestamp()
            
            print(f"StateManager: Replaced result {old_result.youtube_id} with {new_result.youtube_id}")
            
        except ValueError:
            print(f"StateManager: Could not find result {old_result.youtube_id} to replace")
    
    def _remove_result_from_state(self, result: Result, reason: str = "duplicate"):
        """
        Remove a result from state.results and update counters
        """
        try:
            # Remove from results list
            self.state.results.remove(result)
            self._update_results_count()
            
            # Increment duplicate counter
            self.state.totals.duplicates += 1
            
            # Track removed duplicate
            
            # Update timestamp and notify frontend
            self.state.update_timestamp()
            
            print(f"StateManager: Removed duplicate result {result.youtube_id} (reason: {reason}, duplicates: {self.state.totals.duplicates})")
            
        except ValueError:
            print(f"StateManager: Could not find result {result.youtube_id} to remove")
    
    async def _remove_single_tracks(self):
        """
        Remove single tracks when album alternatives exist for the same artist
        Only removes singles (priority 1) if albums/playlists (priority >= 2) exist
        """
        try:
            if not self.state.results:
                return
            
            # Group results by normalized artist
            artist_results = {}
            for result in self.state.results:
                if not result.normalized:
                    continue
                    
                artist = result.normalized.get('artist', '').lower()
                if not artist:
                    continue
                    
                if artist not in artist_results:
                    artist_results[artist] = []
                artist_results[artist].append(result)
            
            # Find singles to remove
            singles_to_remove = []
            
            for artist, results in artist_results.items():
                # Check if this artist has any album-quality content
                has_albums = any(self._get_content_priority(r) >= 2 for r in results)
                
                if has_albums:
                    # Remove singles for this artist since albums exist
                    singles = [r for r in results if self._get_content_priority(r) == 1]
                    singles_to_remove.extend(singles)
            
            # Remove identified singles
            if singles_to_remove:
                removed_count = 0
                for single in singles_to_remove:
                    try:
                        self.state.results.remove(single)
                        removed_count += 1
                        # Increment singles removed counter (quality filtering, not deduplication)
                        self.state.totals.singles_removed += 1
                        
                        # Track removed single
                        
                    except ValueError:
                        continue
                
                if removed_count > 0:
                    # items_found maintains cumulative count - don't overwrite with current length
                    self.state.update_timestamp()
                    
                    print(f"StateManager: Removed {removed_count} single tracks (albums available for same artists) (singles_removed: {self.state.totals.singles_removed})")
                    
                    # Send state update to frontend
                    await self.send_state()
        
        except Exception as e:
            print(f"StateManager: Single track cleanup failed: {e}")
