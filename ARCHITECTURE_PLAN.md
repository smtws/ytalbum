# YouTube Music Downloader - Clean Architecture Rebuild Plan

## Core Principle: Single Source of Truth
ONE data object holds EVERYTHING. Services only modify this object through a central StateManager.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         AppState (Single Object)                 │
│  ┌─────────────┬──────────────┬────────────┬──────────────┐    │
│  │   Config    │   App Status  │  Results[] │   Statistics │    │
│  │  settings   │   searching   │   albums   │   totals     │    │
│  │  debug      │   downloading │   tracks   │   counters   │    │
│  └─────────────┴──────────────┴────────────┴──────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                                ▲
                                │ (Only StateManager modifies)
                                │
                    ┌───────────▼────────────┐
                    │    StateManager        │
                    │  - Single source truth  │
                    │  - WebSocket to Vue    │
                    │  - Service coordinator │
                    └───────────┬────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼
    ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
    │Search Service│   │Verify Service│   │Quality Manager│
    │  - Returns   │   │  - Returns   │   │  - Returns   │
    │    items     │   │    metadata  │   │    decisions │
    └──────────────┘   └──────────────┘   └──────────────┘
```

## Phase 1: Core Infrastructure

### 1.1 AppState Object (`/backend/core/app_state.py`)
```python
class AppState:
    # Application status
    status: str = "idle"  # idle, searching, verifying, downloading, processing
    session_id: str
    
    # Configuration
    config: Config
    debug: bool = False
    
    # Search state
    search_query: str = ""
    search_strategies_completed: List[str] = []
    
    # Results (ordered list, never dict)
    results: List[Result] = []
    
    # Statistics
    total_found: int = 0
    total_verified: int = 0
    total_unverified: int = 0
    duplicates_removed: int = 0
    
    # Download state
    download_queue: List[str] = []
    active_downloads: Dict[str, DownloadStatus] = {}
```

### 1.2 StateManager (`/backend/core/state_manager.py`)
- ONLY component that modifies AppState
- ONLY component that sends WebSocket updates to frontend
- Coordinates all services
- Handles state transitions

Key methods:
- `start_search(query)` - Resets results, starts ALL search services in parallel
- `add_result(item)` - Adds new result (only after YouTube ID deduplication)
- `update_result(id, metadata)` - Updates result with verification data
- `validate_result(item)` - Uses Quality Manager to validate URL accessibility
- `remove_result(id)` - Removes duplicate/low-quality/invalid result
- `send_state()` - Sends complete AppState to frontend

### 1.3 Result Object (`/backend/core/models.py`)
```python
class Result:
    id: str
    youtube_url: str
    title: str
    artist: str
    channel: str
    
    # Verification status
    verified: Optional[bool] = None  # None=pending, True=verified, False=unverified
    verification_metadata: Dict = {}  # MB data, etc.
    
    # Quality metrics
    quality_score: float = 0.0
    view_count: int = 0
    
    # Download info
    download_status: str = "pending"
    output_path: Optional[str] = None
```

## Phase 2: Services (Stateless Workers)

### 2.1 Search Services (`/backend/services/search/`)
Each strategy is a separate service that runs in parallel:
- Receives: artist name, optional album
- Returns: List[Result] with verified=None
- Does NOT send WebSocket updates
- Does NOT modify state

Services (all original search strategies as separate services):
- `channel_search.py` - Find artist channels
- `youtube_music_search.py` - Direct YT Music search
- `playlist_search.py` - Find playlists
- `google_search.py` - Google for YouTube links
- `direct_album_search.py` - Direct artist + album search
- `genre_context_search.py` - Search with genre context
- `alternative_title_search.py` - Search with alternative titles

### 2.2 Verification Services (`/backend/services/verification/`)
- Receives: Result object
- Returns: {verified: bool/None, metadata: dict}
- Does NOT modify the Result directly

Services:
- `musicbrainz_verifier.py` - Check against MusicBrainz (initial implementation)
- `spotify_verifier.py` - (Future) Spotify verification
- `lastfm_verifier.py` - (Future) Last.fm verification
- `discogs_verifier.py` - (Future) Discogs verification

### 2.3 Deduplication & Quality Services (`/backend/services/`)

#### 2.3.1 YouTube ID Deduplicator (`youtube_deduplicator.py`)
- Receives: new_result, existing_results
- Returns: Decision (add_new, reject_duplicate)
- Filters out YouTube ID duplicates BEFORE verification
- Prevents duplicate results from entering the AppState

#### 2.3.2 Quality Manager (`quality_manager.py`)
- Receives: new_result, existing_results
- Returns: Decision (keep_new, replace_id, reject)
- Handles quality-based deduplication
- Calculates quality scores
- Validates YouTube URLs are still accessible (HTTP request)
- Used after verification for quality comparison and URL validation

### 2.4 Download Services (`/backend/services/download/`)
- `downloader.py` - yt-dlp wrapper
- `processor.py` - Audio processing
- `tagger.py` - Metadata tagging

## Phase 3: Frontend (Vue.js)

### 3.1 Single Store (`/frontend/src/stores/app.js`)
```javascript
export const useAppStore = defineStore('app', {
  state: () => ({
    appState: {}  // Complete mirror of backend AppState
  }),
  
  actions: {
    handleWebSocketMessage(data) {
      // Simply replace entire state
      this.appState = data
    }
  }
})
```

### 3.2 Components
- Just display appState data
- Send commands via WebSocket
- NO local state manipulation

## Phase 4: Implementation Steps

### Step 1: Clean Setup
1. Remove ALL existing backend code
2. Remove ALL existing frontend code  
3. Create clean directory structure

### Step 2: Core Implementation
1. Create AppState model
2. Create StateManager
3. Create WebSocket handler
4. Test with dummy data

### Step 3: Search Implementation
1. Create ONE search service (channel_search)
2. Integrate with StateManager
3. Test end-to-end flow
4. Add remaining search services

### Step 4: Verification Implementation
1. Create MusicBrainz verifier
2. Create Quality Manager
3. Test deduplication logic

### Step 5: Frontend Implementation
1. Create Vue app with single store
2. Create results display component
3. Create search input component
4. Connect WebSocket

### Step 6: Download Implementation
1. Create download service
2. Add download queue to AppState
3. Implement progress tracking

## Key Rules

1. **StateManager is God**: ONLY StateManager modifies AppState
2. **Services are Dumb**: Services just process and return data
3. **Frontend is Dumber**: Frontend just displays and sends commands
4. **One WebSocket Message**: Always send complete AppState
5. **No Partial Updates**: Replace entire state on frontend
6. **Verified is Tri-State**: null (pending), true, false
7. **Order Matters**: Results list maintains discovery order
8. **YouTube ID First**: Deduplicate on YouTube ID before verification
9. **Quality Decides**: QualityManager handles quality-based deduplication after verification

## Benefits

1. **Predictable**: One data flow, one source of truth
2. **Debuggable**: Can log every state change in one place
3. **Testable**: Services are pure functions
4. **Scalable**: Easy to add new services
5. **Maintainable**: Clear separation of concerns

## MVP Features (from reference)

Based on `/home/tordt/YT-Downloads-master`:
1. Search for artist albums on YouTube
2. Verify against MusicBrainz
3. Download audio in opus format
4. Remove intro/outro segments
5. Organize in Artist/Album folders
6. Tag with metadata

## What We're NOT Doing
- React (using Vue.js instead)
- TypeScript (optional, keeping it simple)
- Multiple WebSocket message types
- Partial state updates
- Service-to-service communication
- Complex event systems