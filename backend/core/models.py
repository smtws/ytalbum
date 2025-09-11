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


# Clean nested structure models
class CurrentState(BaseModel):
    """Volatile stuff like current action"""
    status: AppStatus = AppStatus.IDLE
    last_activity: str = "Ready to search"
    last_activity_timestamp: datetime = Field(default_factory=datetime.utcnow)
    active_strategies: List[str] = Field(default_factory=list)
    
    # Note: Activity state is tracked via status, last_activity, and active_strategies
    
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
        # Note: This method is now deprecated since strategies_completed/total moved to Totals
        # Use AppState.get_progress_percent() instead
        return 0


class Totals(BaseModel):
    """Strategy progress and results tracking"""
    strategies_completed: int = 0
    strategies_total: int = 0
    results: int = 0
    found: int = 0
    duplicates: int = 0
    singles_removed: int = 0
    metadata_verified: int = 0
    metadata_not_found: int = 0


class Job(BaseModel):
    """Contains stuff like query and timestamp of current search/job"""
    query: str = ""
    started_at: Optional[datetime] = None
    strategies_completed: List[str] = Field(default_factory=list)


class Config(BaseModel):
    """Application configuration"""
    output_directory: str = "/home/tordt/Downloads/YT-Music"
    audio_format: str = "opus"
    audio_quality: str = "best"
    max_parallel_downloads: int = 3
    remove_intro_outro: bool = True
    debug: bool = False
    
    # Search configuration  
    enabled_strategies: List[str] = Field(default_factory=lambda: [
        "channel_search",
        "youtube_music_search", 
        "playlist_search",
        "direct_album_search",
        "google_search",
        "genre_context_search",
        "alternative_title_search"
    ])
    
    # Cookie configuration
    cookie_info: CookieInfo = Field(default_factory=CookieInfo)
    
    # MusicBrainz release prioritization by country (higher values = higher priority)
    release_country_priority: Dict[str, int] = Field(default_factory=lambda: {
        "US": 100,      # United States - often has comprehensive cover art
        "DE": 90,       # Germany - strong music market with good metadata
        "GB": 85,       # Great Britain - major music market
        "XE": 80,       # Europe (general) - good coverage
        "XW": 75,       # Worldwide releases
        "CA": 70,       # Canada
        "AU": 65,       # Australia
        "JP": 60,       # Japan - sometimes has unique releases
        "FR": 55,       # France
        "NL": 50,       # Netherlands
        "SE": 45,       # Sweden - for bands like Sabaton
        "NO": 40,       # Norway
        "FI": 35,       # Finland
        # Add more as needed, lower values for less prioritized countries
    })


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
    
    # Structured enrichment data (separate from verification_metadata for deduplication compatibility)
    normalized: Dict[str, Any] = Field(default_factory=dict)  # From NormalizationService
    metadata: Dict[str, Any] = Field(default_factory=dict)    # From MusicBrainzService
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class AppState(BaseModel):
    """Clean nested structure - single source of truth"""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # Configuration
    config: Config = Field(default_factory=Config)
    
    # Clean nested structure matching your design
    current: CurrentState = Field(default_factory=CurrentState)    # Volatile stuff like current action
    totals: Totals = Field(default_factory=Totals)                # Contains counters
    job: Job = Field(default_factory=Job)                         # Contains stuff like query and timestamp
    results: List[Result] = Field(default_factory=list)          # Speaks for itself
    
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
        # Don't modify counters here - let StateManager handle counting
        self.update_timestamp()
        return True
    
    def remove_result(self, result_id: str) -> bool:
        """Remove result by ID"""
        for i, result in enumerate(self.results):
            if result.id == result_id:
                del self.results[i]
                # Don't modify counters here - let StateManager handle counting
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
            "duplicates_removed": self.totals.duplicates,
            "metadata_verified": self.totals.metadata_verified,
            "metadata_not_found": self.totals.metadata_not_found
        }
    
    def get_progress_percent(self) -> int:
        """Get overall search progress percentage"""
        if self.totals.strategies_total == 0:
            return 0
        return int((self.totals.strategies_completed / self.totals.strategies_total) * 100)