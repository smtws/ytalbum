#!/usr/bin/env python3
"""
YouTube Music Downloader - State Manager
THE ONLY component that modifies AppState. Single source of truth coordinator.
"""

import asyncio
import json
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


class StateManager:
    """
    THE GOD COMPONENT - Only this class modifies AppState
    Coordinates all services and sends updates to frontend
    """
    
    def __init__(self):
        self.state = AppState()
        self.websockets: List[WebSocket] = []
        self.search_tasks: List[asyncio.Task] = []
        
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
        
        # Track normalization tasks for race condition handling
        self.normalization_tasks: Dict[str, asyncio.Task] = {}  # youtube_id -> task
        
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
        if not self.websockets:
            return
        
        # Convert to dict and handle datetime serialization
        state_dict = self.state.model_dump()
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
    
    async def start_search(self, query: str, strategies: List[str]):
        """Start search process - resets state and coordinates all services"""
        print(f"StateManager: Starting search for '{query}'")
        
        # Reset state
        self.state.search_query = query
        self.state.search_started_at = datetime.utcnow()
        self.state.search_strategies_completed = []
        self.state.search_strategies_total = strategies
        self.state.results = []
        self.state.total_found = 0
        self.state.total_verified = 0
        self.state.total_unverified = 0
        self.state.total_failed = 0
        self.state.duplicates_removed = 0
        self.state.status = AppStatus.SEARCHING
        self.state.update_timestamp()
        
        # Reset activity tracking
        self.state.current_state = CurrentState()
        self.state.current_state.strategies_total = len(strategies)
        
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
                    self._run_search_service(strategy, self.state.search_query)
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
            
            results = await service.search(query, cookie_options=cookie_options)
            
            print(f"StateManager: {strategy_name} returned {len(results)} results")
            
            # Process each result through deduplication AND verification
            added_count = 0
            for result in results:
                dedup_result = YouTubeDeduplicator.should_add_result(result, self.state.results)
                
                if dedup_result == "replace":
                    # Better quality duplicate found - replace existing
                    is_available = await self._check_availability_before_adding(result, cookie_options)
                    
                    if is_available:
                        # Find and replace the existing result
                        existing = YouTubeDeduplicator.find_duplicate(result, self.state.results)
                        if existing:
                            # Merge with existing metadata before replacing
                            result = YouTubeDeduplicator.merge_with_existing_metadata(result, existing)
                            
                            # Replace existing result with better quality one
                            existing_index = self.state.results.index(existing)
                            self.state.results[existing_index] = result
                            print(f"StateManager: Replaced {result.youtube_id} with better metadata")
                            
                            # Track the found item (replacement)
                            await self._increment_found(result.title, result.artist)
                            # Trigger normalization if needed
                            await self._trigger_normalization_if_needed(result)
                    else:
                        await self._increment_failed(result.title, "unavailable")
                        
                elif dedup_result == True:
                    # Unique result - add normally
                    is_available = await self._check_availability_before_adding(result, cookie_options)
                    
                    if is_available:
                        success = self.state.add_result(result)
                        if success:
                            added_count += 1
                            # Track the found item
                            await self._increment_found(result.title, result.artist)
                            # Trigger normalization for albums/playlists
                            await self._trigger_normalization_if_needed(result)
                    else:
                        await self._increment_failed(result.title, "unavailable")
                
                # If dedup_result == False, skip (existing duplicate is better)
            
            print(f"StateManager: {strategy_name} added {added_count}/{len(results)} unique results")
            await self._complete_strategy(strategy_name, added_count)
            await self.search_strategy_completed(strategy_name, added_count)
            
        except Exception as e:
            print(f"StateManager: Search service {strategy_name} failed: {e}")
            await self._increment_failed(strategy_name, str(e))
            await self.search_strategy_completed(strategy_name, 0)

    async def add_result(self, result: Result) -> bool:
        """
        Add new result ONLY after YouTube ID deduplication
        Returns True if added, False if duplicate
        """
        # Check for YouTube ID duplicate using deduplicator
        if not YouTubeDeduplicator.should_add_result(result, self.state.results):
            self.state.duplicates_removed += 1
            return False
        
        # Add result
        success = self.state.add_result(result)
        if success:
            print(f"StateManager: Added result {result.id} - {result.title}")
            await self.send_state()
        
        return success
    
    async def update_result(self, result_id: str, **updates) -> bool:
        """Update result with new data (e.g., verification results)"""
        success = self.state.update_result(result_id, **updates)
        if success:
            # Update statistics based on verification status
            if 'verified' in updates:
                self._update_verification_stats()
            
            print(f"StateManager: Updated result {result_id} with {list(updates.keys())}")
            await self.send_state()
        
        return success
    
    async def remove_result(self, result_id: str, reason: str = "") -> bool:
        """Remove result (e.g., failed validation, low quality)"""
        success = self.state.remove_result(result_id)
        if success:
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
        if strategy_name not in self.state.search_strategies_completed:
            self.state.search_strategies_completed.append(strategy_name)
            print(f"StateManager: Strategy '{strategy_name}' completed with {results_count} results")
        
        # Check if all strategies completed
        if len(self.state.search_strategies_completed) >= len(self.state.search_strategies_total):
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
        self.state.status = AppStatus.IDLE  # No need for separate verification phase
        print(f"StateManager: Search completed. Found {self.state.total_found} results")
        
        # Filter out single tracks (keep only multi-track albums/playlists)
        initial_count = len(self.state.results)
        multi_track_results = [r for r in self.state.results if r.track_count is None or r.track_count > 1]
        single_track_count = initial_count - len(multi_track_results)
        
        if single_track_count > 0:
            self.state.results = multi_track_results
            self.state.total_found = len(multi_track_results)
            print(f"StateManager: Filtered out {single_track_count} single tracks, "
                  f"kept {len(multi_track_results)} multi-track results")
        
        # All results are already verified during addition
        stats = self.state.get_statistics()
        print(f"StateManager: All results pre-verified - "
              f"{stats['verified']} available, {stats['unverified']} skipped")
        
        # Set final status and activity
        await self._set_search_status()  # Reset all flags
        
        total_items = len(self.state.results)
        normalized_items = self.state.current_state.items_normalized
        
        if total_items == 0:
            await self._update_activity("Search completed - no results found")
        elif normalized_items > 0:
            await self._update_activity(f"Search completed - found {total_items} items, normalized {normalized_items}")
        else:
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
        self._update_verification_stats()
        
        # Set status back to idle
        self.state.status = AppStatus.IDLE
        
        # Log final stats
        stats = self.state.get_statistics()
        print(f"StateManager: Verification complete - "
              f"{stats['verified']} verified, {stats['unverified']} unavailable")
        
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
        self._update_verification_stats()
        await self.send_state()
    
    def _update_verification_stats(self):
        """Update verification statistics"""
        stats = self.state.get_statistics()
        self.state.total_verified = stats["verified"]
        self.state.total_unverified = stats["unverified"]
        # Keep total_found as is (includes pending)
    
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
        completed = len(self.state.search_strategies_completed)
        total = len(self.state.search_strategies_total)
        
        return {
            "completed_strategies": completed,
            "total_strategies": total,
            "progress_percent": (completed / total * 100) if total > 0 else 0,
            "current_status": self.state.status,
            "results_found": self.state.total_found
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
        if result.verification_metadata and 'normalized_title' in result.verification_metadata:
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
            if current_result.verification_metadata:
                normalized_metadata = NormalizationService.merge_metadata(
                    current_result.verification_metadata,
                    normalized_metadata
                )
            
            # Update the current result with normalized metadata
            current_result.verification_metadata = normalized_metadata
            self.state.update_timestamp()
            
            print(f"StateManager: Normalized {youtube_id} - '{normalized_metadata.get('normalized_title')}'")
            
            # Track normalization completion
            await self._increment_normalized(
                normalized_metadata.get('normalized_title', current_result.title),
                normalized_metadata.get('normalized_artist', current_result.artist)
            )
            
        except asyncio.CancelledError:
            print(f"StateManager: Normalization cancelled for {result.youtube_id}")
        except Exception as e:
            print(f"StateManager: Normalization failed for {result.youtube_id}: {e}")
        finally:
            # Clean up task tracking
            if result.youtube_id in self.normalization_tasks:
                del self.normalization_tasks[result.youtube_id]
    
    # ====== ACTIVITY TRACKING METHODS ======
    
    async def _update_activity(self, message: str):
        """Update current activity and send state"""
        self.state.current_state.update_activity(message)
        await self.send_state()
    
    async def _increment_found(self, title: str, artist: str = ""):
        """Increment found counter and update activity"""
        self.state.current_state.items_found += 1
        
        # Create user-friendly activity message
        if artist:
            activity_msg = f"Found {artist} - {title}"
        else:
            activity_msg = f"Found {title}"
        
        await self._update_activity(activity_msg)
    
    async def _increment_normalized(self, normalized_title: str, artist: str = ""):
        """Increment normalized counter and update activity"""
        self.state.current_state.items_normalized += 1
        
        # Create user-friendly activity message
        if artist:
            activity_msg = f"Normalized {artist} - {normalized_title}"
        else:
            activity_msg = f"Normalized {normalized_title}"
        
        await self._update_activity(activity_msg)
    
    async def _increment_verified(self, title: str, artist: str = "", source: str = "MusicBrainz"):
        """Increment verified counter and update activity"""
        self.state.current_state.items_verified += 1
        
        # Create user-friendly activity message
        if artist:
            activity_msg = f"Verified {artist} - {title} with {source}"
        else:
            activity_msg = f"Verified {title} with {source}"
        
        await self._update_activity(activity_msg)
    
    async def _increment_unverified(self, title: str, artist: str = "", reason: str = ""):
        """Increment unverified counter and update activity"""
        self.state.current_state.items_unverified += 1
        
        # Create user-friendly activity message
        if artist:
            activity_msg = f"Could not verify {artist} - {title}"
        else:
            activity_msg = f"Could not verify {title}"
        
        if reason:
            activity_msg += f" ({reason})"
        
        await self._update_activity(activity_msg)
    
    async def _increment_failed(self, title: str, reason: str = ""):
        """Increment failed counter and update activity"""
        self.state.current_state.items_failed += 1
        
        activity_msg = f"Failed: {title}"
        if reason:
            activity_msg += f" ({reason})"
        
        await self._update_activity(activity_msg)
    
    async def _start_strategy(self, strategy_name: str):
        """Mark strategy as starting"""
        self.state.current_state.active_strategy = strategy_name
        self.state.current_state.is_searching = True
        
        strategy_display = strategy_name.replace('_', ' ').title()
        await self._update_activity(f"Searching with {strategy_display}...")
    
    async def _complete_strategy(self, strategy_name: str, results_count: int):
        """Mark strategy as completed"""
        self.state.current_state.strategies_completed += 1
        self.state.current_state.active_strategy = None
        
        strategy_display = strategy_name.replace('_', ' ').title()
        await self._update_activity(f"Completed {strategy_display} - found {results_count} items")
    
    async def _set_search_status(self, is_searching: bool = False, is_normalizing: bool = False, is_verifying: bool = False):
        """Update search status flags"""
        self.state.current_state.is_searching = is_searching
        self.state.current_state.is_normalizing = is_normalizing
        self.state.current_state.is_verifying = is_verifying
    
