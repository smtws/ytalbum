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

from .models import AppState, AppStatus, Result, SearchStatus
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
        
        # Cancel any existing search tasks
        for task in self.search_tasks:
            if not task.done():
                task.cancel()
        self.search_tasks = []
        
        await self.send_state()
        
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
                if YouTubeDeduplicator.should_add_result(result, self.state.results):
                    # Check availability BEFORE adding to state
                    is_available = await self._check_availability_before_adding(result, cookie_options)
                    
                    if is_available:
                        success = self.state.add_result(result)
                        if success:
                            added_count += 1
                            await self.send_state()  # Send update for each new result
                    else:
                        print(f"StateManager: Skipping unavailable result: {result.title}")
            
            print(f"StateManager: {strategy_name} added {added_count}/{len(results)} unique results")
            await self.search_strategy_completed(strategy_name, added_count)
            
        except Exception as e:
            print(f"StateManager: Search service {strategy_name} failed: {e}")
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
        
        # All results are already verified during addition
        stats = self.state.get_statistics()
        print(f"StateManager: All results pre-verified - "
              f"{stats['verified']} available, {stats['unverified']} skipped")
        
        await self.send_state()
    
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
                    result.status = SearchStatus.VERIFIED
                else:
                    result.status = SearchStatus.UNVERIFIED
                    result.error_message = error_msg
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
                    
                    # Update result status
                    if is_available:
                        result.status = SearchStatus.VERIFIED
                    else:
                        result.status = SearchStatus.UNVERIFIED
                        result.error_message = error_msg
                    
                    # Update cache
                    self.verification_cache.set(result.youtube_url, is_available, error_msg)
                else:
                    # Verification failed
                    result.status = SearchStatus.FAILED
        
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
            result.status = SearchStatus.VERIFIED
            result.error_message = None
        else:
            result.status = SearchStatus.UNVERIFIED
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
    
