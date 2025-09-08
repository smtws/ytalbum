#!/usr/bin/env python3
"""
YouTube Music Downloader - Core Data Models
Single source of truth data structures for the entire application.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import uuid


class SearchStatus(str, Enum):
    """Search result verification status"""
    PENDING = "pending"
    VERIFYING = "verifying" 
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    FAILED = "failed"


class DownloadStatus(str, Enum):
    """Download progress status"""
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class AppStatus(str, Enum):
    """Application state status"""
    IDLE = "idle"
    SEARCHING = "searching"
    VERIFYING = "verifying"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"


class CookieInfo(BaseModel):
    """Cookie detection and status information (detected once on startup)"""
    browsers_detected: List[str] = Field(default_factory=list)
    recommended_browser: Optional[str] = None
    cookie_files_count: int = 0
    cookie_support_available: bool = False
    yt_dlp_compatible: bool = False


class CurrentState(BaseModel):
    """Real-time activity and progress tracking for user feedback"""
    # Activity counts
    items_found: int = 0
    items_normalized: int = 0
    items_verified: int = 0
    items_unverified: int = 0
    items_failed: int = 0
    
    # Current activity status
    last_activity: str = "Ready to search"
    last_activity_timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # Progress indicators
    is_searching: bool = False
    is_normalizing: bool = False
    is_verifying: bool = False
    
    # Search strategy progress
    active_strategy: Optional[str] = None
    strategies_completed: int = 0
    strategies_total: int = 0
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
    
    def update_activity(self, message: str):
        """Update last activity with timestamp"""
        self.last_activity = message
        self.last_activity_timestamp = datetime.utcnow()
    
    def get_progress_percent(self) -> int:
        """Get overall progress percentage"""
        if self.strategies_total == 0:
            return 0
        return int((self.strategies_completed / self.strategies_total) * 100)


class Config(BaseModel):
    """Application configuration"""
    output_directory: str = "/home/tordt/Downloads/YT-Music"
    audio_format: str = "opus"
    audio_quality: str = "best"
    max_parallel_downloads: int = 3
    remove_intro_outro: bool = True
    debug: bool = False
    
    # Cookie configuration
    cookie_info: CookieInfo = Field(default_factory=CookieInfo)


class Result(BaseModel):
    """Single search result with verification data"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    youtube_url: str
    youtube_id: str  # Extracted from URL for deduplication
    title: str
    artist: str
    channel: str
    channel_id: Optional[str] = None
    
    # Verification status
    verified: Optional[bool] = None  # None=pending, True=verified, False=unverified
    verification_metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # Quality metrics
    quality_score: float = 0.0
    thumbnail_url: Optional[str] = None
    track_count: Optional[int] = None  # playlist_count for playlists, chapter_count for videos
    
    # Discovery metadata
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    discovered_by: str = ""  # which search service found this
    
    # Download info
    download_status: DownloadStatus = DownloadStatus.QUEUED
    output_path: Optional[str] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class AppState(BaseModel):
    """Single source of truth - all application state"""
    # Application status
    status: AppStatus = AppStatus.IDLE
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # Configuration
    config: Config = Field(default_factory=Config)
    
    # Real-time activity tracking
    current_state: CurrentState = Field(default_factory=CurrentState)
    
    # Search state
    search_query: str = ""
    search_started_at: Optional[datetime] = None
    search_strategies_completed: List[str] = Field(default_factory=list)
    search_strategies_total: List[str] = Field(default_factory=list)
    
    # Results (ordered list - NEVER dict)
    results: List[Result] = Field(default_factory=list)
    
    # Statistics (legacy - use current_state for real-time)
    total_found: int = 0
    total_verified: int = 0
    total_unverified: int = 0
    total_failed: int = 0
    duplicates_removed: int = 0
    
    # Download state
    download_queue: List[str] = Field(default_factory=list)  # result IDs
    active_downloads: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
    
    def update_timestamp(self):
        """Update the last modified timestamp"""
        self.updated_at = datetime.utcnow()
    
    def get_result_by_id(self, result_id: str) -> Optional[Result]:
        """Get result by ID"""
        for result in self.results:
            if result.id == result_id:
                return result
        return None
    
    def get_result_by_youtube_id(self, youtube_id: str) -> Optional[Result]:
        """Get result by YouTube ID (for deduplication)"""
        for result in self.results:
            if result.youtube_id == youtube_id:
                return result
        return None
    
    def add_result(self, result: Result) -> bool:
        """Add result if YouTube ID doesn't exist"""
        if self.get_result_by_youtube_id(result.youtube_id):
            return False  # Duplicate
        
        self.results.append(result)
        self.total_found += 1
        self.update_timestamp()
        return True
    
    def remove_result(self, result_id: str) -> bool:
        """Remove result by ID"""
        for i, result in enumerate(self.results):
            if result.id == result_id:
                del self.results[i]
                self.total_found -= 1
                self.duplicates_removed += 1
                self.update_timestamp()
                return True
        return False
    
    def update_result(self, result_id: str, **updates) -> bool:
        """Update result fields"""
        result = self.get_result_by_id(result_id)
        if not result:
            return False
        
        for key, value in updates.items():
            if hasattr(result, key):
                setattr(result, key, value)
        
        self.update_timestamp()
        return True
    
    def get_statistics(self) -> Dict[str, int]:
        """Calculate current statistics"""
        verified = sum(1 for r in self.results if r.verified is True)
        unverified = sum(1 for r in self.results if r.verified is False)
        pending = sum(1 for r in self.results if r.verified is None)
        
        return {
            "total_found": len(self.results),
            "verified": verified,
            "unverified": unverified,
            "pending": pending,
            "duplicates_removed": self.duplicates_removed
        }