/**
 * TypeScript types for the YouTube Downloader AppState
 * Generated from actual backend AppState JSON structure
 * 
 * This is the single source of truth from the backend
 * Frontend only displays this data, never modifies it
 */

// Main AppState that comes via WebSocket - Clean nested structure
export interface AppState {
  session_id: string;
  config: Config;
  current: CurrentState;  // Volatile stuff like current action (including status)
  totals: Totals;         // Contains counters - single source of truth
  job: Job;               // Contains stuff like query and timestamp
  results: Result[];      // Speaks for itself
  download_queue: string[];
  active_downloads: Record<string, any>;
  created_at: string;
  updated_at: string;
}

// Status enum
export enum AppStatus {
  IDLE = "idle",
  SEARCHING = "searching",
  DOWNLOADING = "downloading",
  ERROR = "error"
}

// Configuration
export interface Config {
  output_directory: string;
  audio_format: string;
  audio_quality: string;
  max_parallel_downloads: number;
  remove_intro_outro: boolean;
  debug: boolean;
  enabled_strategies: string[];
  cookie_info: CookieInfo;
}

export interface CookieInfo {
  browsers_detected: string[];
  recommended_browser: string | null;
  cookie_files_count: number;
  cookie_support_available: boolean;
  yt_dlp_compatible: boolean;
}

// New nested interfaces matching backend structure
export interface Totals {
  strategies_completed: number;
  strategies_total: number;
  results: number;
  found: number;
  duplicates: number;
  singles_removed: number;
}

export interface Job {
  query: string;
  started_at: string | null;
  strategies_completed: string[];
}

// Current state tracking - volatile UI state
export interface CurrentState {
  status: AppStatus;
  last_activity: string;
  last_activity_timestamp: string;
  active_strategies: string[];
  // Note: Activity state is tracked via status, last_activity, and active_strategies
}

// Result item from search
export interface Result {
  id: string;
  youtube_url: string;
  youtube_id: string;
  title: string;
  artist: string;
  channel: string;
  channel_id: string | null;
  verified: boolean | null;
  verification_metadata?: VerificationMetadata;
  quality_score: number;
  thumbnail_url: string | null;
  track_count: number | null;
  discovered_at: string;
  discovered_by: string;
  download_status: DownloadStatus;
  output_path: string | null;
  // Structured enrichment data (from backend)
  normalized: Record<string, any>;  // From NormalizationService
  metadata: Record<string, any>;    // From MusicBrainzService
}

// Verification metadata (from normalization and MusicBrainz)
export interface VerificationMetadata {
  // Normalization data
  normalized_at?: string;
  original_title?: string;
  original_artist?: string;
  normalized_title?: string;
  normalized_artist?: string;
  normalized_channel?: string;
  extracted_features?: string | null;
  extracted_version?: string | null;
  
  // MusicBrainz data
  service?: string;
  retrieved_at?: string;
  id?: string;
  mb_title?: string;
  mb_artist?: string;
  mb_date?: string;
  mb_country?: string;
  mb_status?: string;
  mb_packaging?: string;
  mb_track_count?: number;
  mb_release_group_id?: string;
  mb_release_group_type?: string;
  mb_quality?: string;
  mb_not_found?: string;
}

// Download status enum
export enum DownloadStatus {
  QUEUED = "queued",
  DOWNLOADING = "downloading",
  COMPLETED = "completed",
  FAILED = "failed",
  SKIPPED = "skipped"
}

// WebSocket message types
export interface WebSocketMessage {
  type: MessageType;
  payload?: any;
}

export enum MessageType {
  // From backend
  STATE_UPDATE = "state_update",
  ERROR = "error",
  SUCCESS = "success",
  
  // From frontend (commands)
  SEARCH = "search",
  DOWNLOAD = "download",
  DOWNLOAD_ALL = "download_all",
  CANCEL_DOWNLOAD = "cancel_download",
  CLEAR_QUEUE = "clear_queue",
  CLEAR_RESULTS = "clear_results",
  UPDATE_CONFIG = "update_config"
}

// Command payloads
export interface SearchCommand {
  query: string;
}

export interface DownloadCommand {
  result_id: string;
}

export interface UpdateConfigCommand {
  config: Partial<Config>;
}