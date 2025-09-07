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


class StateManager:
    """
    THE GOD COMPONENT - Only this class modifies AppState
    Coordinates all services and sends updates to frontend
    """
    
    def __init__(self):
        self.state = AppState()
        self.websockets: List[WebSocket] = []
        self.search_tasks: List[asyncio.Task] = []
        
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
        
        # TODO: Start all search services in parallel
        print(f"StateManager: Would start {len(strategies)} search services")
    
    async def add_result(self, result: Result) -> bool:
        """
        Add new result ONLY after YouTube ID deduplication
        Returns True if added, False if duplicate
        """
        # Check for YouTube ID duplicate
        existing = self.state.get_result_by_youtube_id(result.youtube_id)
        if existing:
            print(f"StateManager: Rejected duplicate YouTube ID {result.youtube_id}")
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
    
    async def search_completed(self):
        """All search strategies completed"""
        self.state.status = AppStatus.VERIFYING
        print(f"StateManager: Search completed. Found {self.state.total_found} results")
        
        # TODO: Start verification process
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