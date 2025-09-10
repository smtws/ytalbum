# YouTube Music Downloader - Internal API Reference

This document serves as an internal reference for the YouTube Music Downloader codebase architecture, APIs, and data structures.

**Current System Status:** ✅ **FULLY OPERATIONAL**
- **Search System:** All 7 search services functioning correctly
- **Counter Synchronization:** Mathematical accuracy guaranteed (found = results + duplicates + singles_removed)
- **Cache Management:** Fresh cache clearing on each search start
- **Rate Limiting:** 0.5s delay between yt-dlp calls (2 requests/second)
- **Real-time Updates:** WebSocket state synchronization working properly
- **Error Recovery:** All debug artifacts removed, syntax errors resolved

## System Architecture Overview

### Core Components

1. **FastAPI Backend** (`/backend/app.py`) - WebSocket-based real-time API server
2. **StateManager** (`/backend/core/state_manager.py`) - Central state coordinator and single source of truth with mathematical counter accuracy
3. **Vue.js Frontend** (`/frontend/src/`) - Real-time UI consuming WebSocket state updates
4. **7-Service Search Architecture** - Comprehensive YouTube search strategy system with parallel execution
5. **Verification & Processing Pipeline** - Multi-stage result validation and metadata enrichment with cache clearing
6. **Rate Limiting System** - Centralized yt-dlp request throttling (2 req/s) via YTDLPRateLimiter

## WebSocket API

### Endpoint
- **URL**: `ws://localhost:{port}/ws`
- **Connection**: Persistent WebSocket for real-time bidirectional communication

### Message Format
```json
{
  "type": "message_type",
  "data": { /* payload */ },
  "timestamp": "ISO_8601_timestamp"
}
```

### Frontend → Backend Commands

#### Start Search
```json
{
  "type": "start_search",
  "data": {
    "query": "artist_name"
  }
}
```

#### Clear Results
```json
{
  "type": "clear_results",
  "data": {}
}
```

#### Get Current State
```json
{
  "type": "get_state",
  "data": {}
}
```

#### Test with Dummy Data
```json
{
  "type": "test_dummy_data",
  "data": {}
}
```

### Backend → Frontend Messages

#### State Update (Primary Message)
```json
{
  "type": "state_update",
  "data": {
    /* Complete AppState object - see Data Models section */
  },
  "timestamp": "2024-01-01T12:00:00.000Z"
}
```

## REST API Endpoints

### Health Check
```
GET /health
Returns: {
  "status": "healthy",
  "connected_clients": number,
  "current_state": "idle|searching|downloading",
  "results_count": number,
  "cookie_support": {
    "browsers_detected": string[],
    "recommended_browser": string,
    "cookie_support_available": boolean,
    "yt_dlp_compatible": boolean
  }
}
```

### Clear State
```
POST /clear
Returns: {
  "status": "cleared",
  "message": "All search results and state cleared"
}
```

### Static Files
```
GET / - Serves Vue.js frontend (if built)
GET /static/* - Static assets
```

## Data Models

### AppState (Central Data Structure)
```typescript
interface AppState {
  session_id: string;
  config: Config;
  current: CurrentState;  // Volatile state
  totals: Totals;         // Counters and metrics
  job: Job;               // Current search job details
  results: Result[];      // Search results array
  download_queue: string[];
  active_downloads: Record<string, any>;
  created_at: string;
  updated_at: string;
}
```

### CurrentState (Volatile UI State)
```typescript
interface CurrentState {
  status: "idle" | "searching" | "verifying" | "downloading" | "processing";
  last_activity: string;
  last_activity_timestamp: string;
  active_strategies: string[];  // Currently running search services
}
```

### Totals (Metrics & Counters)
```typescript
interface Totals {
  strategies_completed: number;
  strategies_total: number;
  results: number;        // Total items in results array
  found: number;          // Items that passed availability verification
  duplicates: number;     // Items removed by post-processing deduplication
  singles_removed: number; // Items removed by singles cleanup (quality filtering)
}
```

### Job (Search Session Details)
```typescript
interface Job {
  query: string;
  started_at: string | null;
  strategies_completed: string[];
}
```

### Result (Search Result Item)
```typescript
interface Result {
  id: string;                    // UUID
  youtube_url: string;
  youtube_id: string;           // For deduplication
  title: string;
  artist: string;
  channel: string;
  channel_id: string | null;
  
  // Verification status
  verified: boolean | null;     // null=pending, true=verified, false=failed
  verification_metadata: VerificationMetadata;
  
  // Quality metrics
  quality_score: number;        // 0.0-1.0 relevance score
  thumbnail_url: string | null;
  track_count: number | null;
  
  // Discovery metadata
  discovered_at: string;
  discovered_by: string;        // Which search service found this
  
  // Download info
  download_status: "queued" | "downloading" | "completed" | "failed";
  output_path: string | null;
}
```

### Config (Application Settings)
```typescript
interface Config {
  output_directory: string;
  audio_format: string;
  audio_quality: string;
  max_parallel_downloads: number;
  remove_intro_outro: boolean;
  debug: boolean;
  enabled_strategies: string[];  // Active search services
  cookie_info: CookieInfo;
}
```

## Search Service Architecture

### Available Services (7 Total)
1. **DirectAlbumSearchService** - Exact artist+album matching (0.9 quality score)
2. **PlaylistSearchService** - Album playlists from channels/users (variable quality)  
3. **GenreContextSearchService** - Genre-aware artist searches (0.75 quality)
4. **YouTubeMusicSearchService** - YouTube Music API searches (0.5-1.0 quality)
5. **ChannelSearchService** - Artist channel discovery (0.9 quality)
6. **AlternativeTitleSearchService** - Name variations/misspellings (0.6 quality)
7. **GoogleSearchService** - Google-style fallback searches (0.4-0.6 quality)

### Common Service Interface
```python
class SearchService:
    def __init__(self):
        self.service_name = "service_name"
    
    async def search(
        self,
        artist: str,
        album: Optional[str] = None,
        cookie_options: Optional[Dict[str, Any]] = None,
        ytdlp_executor: Optional[callable] = None
    ) -> List[Result]:
        """Main search method"""
        pass
    
    def get_service_info(self) -> Dict[str, Any]:
        """Return service metadata"""
        pass
```

### Search Flow
1. **StateManager.start_search()** triggered by WebSocket message
2. **Parallel execution** of all enabled search services via `asyncio.create_task()`
3. **Rate-limited yt-dlp calls** through shared `ytdlp_executor`
4. **Initial deduplication** via `YouTubeDeduplicator` (based on `youtube_id`)
5. **Availability verification** via `AvailabilityVerificationService`
6. **Metadata enrichment** via `NormalizationService` and `MusicBrainzService`
7. **Post-processing deduplication** (after normalization and MusicBrainz enrichment)
8. **Real-time updates** sent to frontend via WebSocket

#### Post-Processing Deduplication Pipeline
The system performs additional deduplication after metadata enrichment:

- **After Normalization**: Removes duplicates discovered through title/artist normalization
- **After MusicBrainz Enrichment**: Removes duplicates with identical MusicBrainz IDs (MBID)
- **Duplicate Counter**: `_remove_result_from_state()` increments `totals.duplicates` for each removed item
- **Quality Preservation**: Always keeps the highest quality result when removing duplicates

#### Singles Cleanup (Quality Filtering)
Separate from deduplication, the system performs quality filtering:

- **Purpose**: Removes single tracks when album alternatives exist for the same artist
- **Trigger**: Called after search completion and after MusicBrainz enrichment  
- **Logic**: Only removes singles (priority 1) if albums/playlists (priority ≥ 2) exist
- **Singles Counter**: `_remove_single_tracks()` increments `totals.singles_removed` for each removed item
- **Not Duplicates**: Singles removal is quality filtering, not deduplication

## Core Services

### StateManager (Central Coordinator) ✅ FULLY OPERATIONAL
- **Role**: Single source of truth for AppState modifications with mathematical accuracy
- **Counter Synchronization**: Guaranteed mathematical accuracy - found = results + duplicates + singles_removed
- **Cache Management**: Automatic cache clearing on search start for fresh results
- **Key Methods**:
  - `start_search(query: str)` - Initiates multi-service search with cache clearing
  - `add_result(result: Result)` - Adds result with deduplication and counter sync
  - `update_result(result_id: str, **updates)` - Updates result fields
  - `send_state()` - Broadcasts current state to all WebSocket clients
  - `clear_state()` - Resets application state
  - `_update_results_count()` - Synchronizes results counter with actual array length
  - `_remove_result_from_state(result: Result)` - Removes duplicate results and increments duplicates counter
  - `_remove_single_tracks()` - Removes single tracks when album alternatives exist and increments singles_removed counter
  - `execute_ytdlp_command()` - Centralized rate-limited yt-dlp execution (0.5s delay)

### YouTubeDeduplicator
- **Purpose**: Prevents duplicate results based on `youtube_id`
- **Method**: `deduplicate(results: List[Result]) -> List[Result]`

### AvailabilityVerificationService
- **Purpose**: Verifies YouTube URLs are accessible and retrieves metadata
- **Features**: Caching (30min), rate limiting, error handling
- **Method**: `verify_bulk(results: List[Result]) -> List[Result]`

### NormalizationService
- **Purpose**: Standardizes titles/artists, extracts features/versions
- **Features**: Metadata cache (24hr), artist/title normalization
- **Method**: `normalize_bulk(results: List[Result]) -> List[Result]`

### MusicBrainzService
- **Purpose**: Enriches results with MusicBrainz metadata
- **Features**: Release matching, metadata cache, quality scores
- **Method**: `enrich_bulk(results: List[Result]) -> List[Result]`

## Frontend Architecture

### Key Components
- **App.vue** - Main application component with WebSocket integration
- **SearchBar.vue** - Search input and submission
- **ActivityPanel.vue** - Real-time search progress display
- **ResultsGrid.vue** - Search results display grid
- **ResultCard.vue** - Individual result card

### WebSocket Service (`/frontend/src/services/WebSocketService.ts`)
```typescript
class WebSocketService {
  connect(): void                    // Connect to backend WebSocket
  disconnect(): void                 // Close WebSocket connection
  search(query: string): void        // Send search command
  testDummyData(): void             // Load test data
  // ... other command methods
}

// Exported reactive state
export const isConnected: Ref<boolean>
export const appState: Ref<AppState | null>
export const connectionError: Ref<string | null>
```

## State Management Flow

### Search Workflow
1. User enters query in `SearchBar.vue`
2. Frontend sends `start_search` WebSocket message
3. `StateManager` receives message and calls `start_search(query)`
4. Duplicate search prevention check (if already searching)
5. All 7 search services execute in parallel
6. Results processed through verification pipeline
7. State updates sent to frontend via WebSocket in real-time
8. Frontend reactively updates UI components

### State Update Pattern
```typescript
// Backend (StateManager)
this.state.current.status = AppStatus.SEARCHING;
this.state.current.update_activity("Searching for music...");
await this.send_state();  // Broadcasts to all WebSocket clients

// Frontend (reactive updates)
watch(appState, (newState) => {
  // UI automatically updates when appState changes
});
```

## Development Guidelines

### Adding New Search Services
1. Create service in `/backend/services/search/`
2. Implement common interface (`search()`, `get_service_info()`)
3. Register in `StateManager.__init__()` 
4. Add to default `enabled_strategies` in `Config` model
5. Test with comprehensive artist searches

### Extending Data Models
1. Update Pydantic models in `/backend/core/models.py`
2. Update TypeScript interfaces in `/frontend/src/types/AppState.ts`
3. Ensure JSON serialization compatibility
4. Test WebSocket message format

### WebSocket Message Types
- Follow existing pattern: `{"type": "action_name", "data": {...}}`
- Handle in `handle_websocket_message()` function
- Add TypeScript types for new messages
- Update this documentation

## Error Handling

### Search Service Errors
- Individual service failures don't crash entire search
- Services handle their own exceptions and log errors
- Failed services simply contribute 0 results
- Search continues with successful services

### WebSocket Errors  
- Connection failures handled gracefully in frontend
- Automatic reconnection attempts
- Error states displayed to user
- Backend continues running during client disconnections

### Verification Pipeline Errors
- Individual result verification failures are logged
- Failed results marked as `verified: false`
- Pipeline continues processing remaining results
- Bulk operations with individual error isolation

## Recent System Updates & Fixes (Current Status)

### ✅ Counter Synchronization System (RESOLVED)
**Issue:** Mathematical discrepancy in totals where found ≠ results + duplicates + singles_removed  
**Root Cause:** `_update_results_count()` was only called during result removal, not during addition  
**Solution:** Added `self._update_results_count()` call immediately after successful result addition in `_run_search_service()`  
**Status:** ✅ **FIXED** - Mathematical accuracy now guaranteed

### ✅ Cache Management System (IMPLEMENTED)
**Feature:** Automatic cache clearing on search start for fresh results  
**Implementation:** Added cache clearing in `start_search()` method for:
- `verification_cache.cache.clear()`
- `normalization_service.cache.clear()` 
- `musicbrainz_service.cache.clear()`  
**Status:** ✅ **ACTIVE** - Fresh cache on every search

### ✅ Debug Artifact Cleanup (RESOLVED)
**Issue:** Search system completely broken - stopped at 6 results instead of ~60  
**Root Cause:** Debug cleanup accidentally left broken references and syntax errors:
- Non-existent debug variables (`self.found_item_index`, `self.removed_duplicates_index`, `self.removed_singles_index`)
- Missing debug methods (`_debug_found_counter_consistency()`, `_write_duplicates_log()`, `_write_singles_log()`)  
- Orphaned `except Exception as e:` block without corresponding `try` statement
- Missing `NormalizationService` initialization  
**Solution:** Complete cleanup of all debug references and syntax fixes  
**Status:** ✅ **RESOLVED** - Search system fully operational

### ✅ Rate Limiting System (OPERATIONAL)
**Feature:** Centralized yt-dlp rate limiting via `YTDLPRateLimiter`  
**Configuration:** 0.5s delay between requests (2 requests/second)  
**Implementation:** All search services use `execute_ytdlp_command()` for consistent throttling  
**Status:** ✅ **ACTIVE** - Prevents API rate limit violations

### ✅ Graceful Application Restart (IMPLEMENTED)
**Tool:** `restart_app.py` script for clean process management  
**Features:** 
- Kills processes on ports 7002 (backend) and 5173 (frontend)
- Graceful SIGTERM followed by SIGKILL if needed
- Automatic restart of both services with proper environment variables  
**Status:** ✅ **AVAILABLE** - Use `python3 restart_app.py`

---

**System Health:** All core functionalities restored and operational. Expected search performance: ~60 results for comprehensive queries like "sabaton".