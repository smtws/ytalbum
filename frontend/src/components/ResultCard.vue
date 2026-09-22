<template>
  <div class="result-card" :class="cardClasses">
    <!-- Fixed Height Header (4 lines) -->
    <div class="card-header">
      <div class="info-section">
        <div class="artist" :class="artistClasses">
          {{ displayArtist }}
        </div>
        <div class="title" :class="titleClasses">
          {{ displayTitle }}
        </div>
      </div>
      <div class="verification-indicator" :class="verificationClass">
        {{ verificationIcon }}
      </div>
    </div>
    
    <!-- Image Container -->
    <div class="image-container">
      <div class="image-wrapper">
        <!-- Cover Art (when available) -->
        <img 
          v-if="coverArtUrl" 
          :src="coverArtUrl" 
          :alt="displayTitle"
          class="cover-art"
          :class="{ 'fade-in': showCoverArt }"
        />
        <!-- YouTube Thumbnail (fallback) -->
        <img 
          v-else-if="result.thumbnail_url" 
          :src="result.thumbnail_url" 
          :alt="displayTitle"
          class="thumbnail"
        />
        <!-- Placeholder -->
        <div v-else class="image-placeholder">
          🎵
        </div>
      </div>
    </div>
    
    <!-- Card Footer -->
    <div class="card-footer">
      <!-- Label and Year -->
      <div class="metadata-line">
        <span v-if="displayLabel" class="label">{{ displayLabel }}</span>
        <span v-if="displayYear" class="year">{{ displayYear }}</span>
      </div>
      
      <!-- Track Count Comparison -->
      <div v-if="showTrackComparison" class="track-comparison" :class="trackComparisonClass">
        MB:{{ mbTrackCount }} / YT:{{ ytTrackCount }}
      </div>
      <div v-else-if="result.track_count" class="track-count">
        {{ result.track_count }} tracks
      </div>
      
      <!-- Actions -->
      <div class="actions">
        <a 
          :href="result.youtube_url"
          target="_blank"
          class="btn-youtube"
          rel="noopener noreferrer"
        >
          YouTube
        </a>
        <button 
          @click="$emit('download')"
          class="btn-download"
          :disabled="result.download_status === 'downloading'"
        >
          {{ downloadButtonText }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { Result, DownloadStatus } from '../types/AppState';

interface Props {
  result: Result;
}

interface Emits {
  (e: 'download'): void;
}

const props = defineProps<Props>();
defineEmits<Emits>();

// Progressive Enhancement - Display Values
const displayArtist = computed(() => {
  // Stage 3: Metadata (best) -> Stage 2: Normalized -> Stage 1: Raw
  if (props.result.metadata?.normalized?.artist) {
    return props.result.metadata.normalized.artist;
  }
  if (props.result.normalized?.artist) {
    return props.result.normalized.artist;
  }
  return props.result.artist; // Raw YouTube data
});

const displayTitle = computed(() => {
  // Stage 3: Metadata (best) -> Stage 2: Normalized -> Stage 1: Raw
  if (props.result.metadata?.normalized?.title) {
    return props.result.metadata.normalized.title;
  }
  if (props.result.normalized?.title) {
    return props.result.normalized.title;
  }
  return props.result.title; // Raw YouTube data
});


// Progressive Enhancement - Styling Classes
const artistClasses = computed(() => ({
  'raw': !props.result.normalized?.artist,
  'normalized': props.result.normalized?.artist && !props.result.metadata?.normalized?.artist,
  'verified': !!props.result.metadata?.normalized?.artist
}));

const titleClasses = computed(() => ({
  'raw': !props.result.normalized?.title,
  'normalized': props.result.normalized?.title && !props.result.metadata?.normalized?.title,
  'verified': !!props.result.metadata?.normalized?.title
}));


const cardClasses = computed(() => ({
  'stage-1': !props.result.normalized?.title,
  'stage-2': props.result.normalized?.title && !props.result.metadata?.id && !props.result.metadata,
  'stage-2-failed': props.result.normalized?.title && props.result.metadata && !props.result.metadata?.id,
  'stage-3': !!props.result.metadata?.id,
  'has-cover-art': !!coverArtUrl.value
}));

// Image Enhancement
const coverArtUrl = computed(() => {
  // Check new normalized structure first
  if (props.result.metadata?.normalized?.cover_art?.front_cover) {
    return props.result.metadata.normalized.cover_art.front_cover;
  }
  
  // Fall back to direct cover_art_urls structure 
  if (props.result.metadata?.cover_art_urls?.front) {
    return props.result.metadata.cover_art_urls.front;
  }
  
  // Try smaller sizes as fallback
  if (props.result.metadata?.cover_art_urls?.front_500) {
    return props.result.metadata.cover_art_urls.front_500;
  }
  
  if (props.result.metadata?.cover_art_urls?.front_250) {
    return props.result.metadata.cover_art_urls.front_250;
  }
  
  return null;
});

const showCoverArt = computed(() => {
  return !!coverArtUrl.value;
});

// Metadata Display
const displayLabel = computed(() => {
  // Try multiple possible label fields
  return props.result.metadata?.normalized?.label || 
         props.result.metadata?.label || 
         null;
});

const displayYear = computed(() => {
  if (props.result.metadata?.normalized?.release_year) {
    return props.result.metadata.normalized.release_year;
  }
  if (props.result.metadata?.date) {
    return new Date(props.result.metadata.date).getFullYear();
  }
  return null;
});

// Track Count Comparison
const ytTrackCount = computed(() => props.result.track_count || 0);
const mbTrackCount = computed(() => props.result.metadata?.normalized?.track_count || 
                                   props.result.metadata?.['track-count'] || 0);

const showTrackComparison = computed(() => {
  return ytTrackCount.value > 0 && mbTrackCount.value > 0;
});

const trackComparisonClass = computed(() => {
  if (!showTrackComparison.value) return '';
  
  const yt = ytTrackCount.value;
  const mb = mbTrackCount.value;
  
  if (yt === mb) return 'perfect-match';  // Green
  if (yt > mb) return 'bonus-content';    // Blue
  if (yt < mb && (mb - yt) <= 2) return 'minor-incomplete'; // Orange
  return 'major-discrepancy'; // Red
});

// Verification Status
const verificationClass = computed(() => {
  if (props.result.metadata?.id) return 'verified';
  if (props.result.normalized?.title) {
    // Check if this is a failed validation (normalized but no metadata after processing)
    if (props.result.metadata && !props.result.metadata.id) return 'failed';
    return 'processing';
  }
  return 'pending';
});

const verificationIcon = computed(() => {
  if (props.result.metadata?.id) return '✓';
  if (props.result.normalized?.title) {
    // Check if this is a failed validation (normalized but no metadata after processing)
    if (props.result.metadata && !props.result.metadata.id) return '✗';
    return '⟳';
  }
  return '?';
});

// Utilities
const downloadButtonText = computed(() => {
  switch (props.result.download_status) {
    case 'downloading': return 'Downloading...';
    case 'completed': return 'Downloaded';
    case 'failed': return 'Failed - Retry';
    case 'skipped': return 'Skipped';
    default: return 'Download';
  }
});

const formatSource = (source: string): string => {
  return source
    .replace(/_/g, ' ')
    .replace(/\b\w/g, l => l.toUpperCase());
};
</script>

<style scoped>
/* Card Layout */
.result-card {
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  overflow: hidden;
  transition: all 0.3s ease;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.result-card:hover {
  box-shadow: 0 4px 16px rgba(0,0,0,0.15);
  transform: translateY(-2px);
}

/* Progressive Enhancement Stages */
.result-card.stage-1 {
  border-left: 4px solid #ccc;
}

.result-card.stage-2 {
  border-left: 4px solid #f39c12;
}

.result-card.stage-2-failed {
  border-left: 4px solid #999;
}

.result-card.stage-3 {
  border-left: 4px solid #42b883;
}

/* Card Header - Fixed height for longer titles */
.card-header {
  padding: 1rem 1rem 1.5rem 1rem; /* Added extra bottom padding */
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  border-bottom: 1px solid #f0f0f0;
  height: 7.5rem; /* Increased by 0.5rem */
  overflow: hidden;
}

.info-section {
  flex: 1;
  min-width: 0; /* Allow text truncation */
}

.verification-indicator {
  width: 2rem;
  height: 2rem;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  color: white;
  font-size: 1rem;
  flex-shrink: 0;
}

.verification-indicator.pending {
  background: #999;
}

.verification-indicator.processing {
  background: #f39c12;
  animation: pulse 2s infinite;
}

.verification-indicator.failed {
  background: #999;
}

.verification-indicator.verified {
  background: #42b883;
}

@keyframes pulse {
  0% { opacity: 1; }
  50% { opacity: 0.6; }
  100% { opacity: 1; }
}

/* Progressive Text Styling */
.artist, .title {
  margin: 0;
  line-height: 1.3;
  transition: all 0.3s ease;
}

/* Artist: Single line with ellipsis */
.artist {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 0.25rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Title: Max 3 lines with ellipsis */
.title {
  font-size: 1.1rem;
  font-weight: 700;
  margin-bottom: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  line-height: 1.3;
}

/* Stage 1: Raw Data */
.artist.raw, .title.raw {
  color: #666;
  font-style: italic;
  font-weight: 400;
}

/* Stage 2: Normalized */
.artist.normalized, .title.normalized {
  color: #333;
  font-weight: 600;
}

/* Stage 3: Verified */
.artist.verified, .title.verified {
  color: #2c3e50;
  font-weight: 700;
}


/* Image Container */
.image-container {
  position: relative;
  flex: 1;
  display: block; /* Remove flex centering */
  background: #f8f9fa;
}

.image-wrapper {
  width: 100%;
  aspect-ratio: 1; /* Square container */
  position: relative;
  overflow: hidden;
  background: #000; /* Black background for letterboxing */
}

/* Cover Art - Fill entire square */
.cover-art {
  width: 100%;
  height: 100%;
  object-fit: cover; /* Fill entire square, crop if needed */
  display: block;
  transition: opacity 0.5s ease;
}

/* YouTube Thumbnail - Maintain aspect ratio with letterboxing */
.thumbnail {
  width: 100%;
  height: 100%;
  object-fit: contain; /* Maintain aspect ratio, add borders if needed */
  display: block;
}

.cover-art.fade-in {
  animation: fadeIn 0.5s ease;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.image-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 3rem;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

/* Card Footer */
.card-footer {
  padding: 1rem;
  border-top: 1px solid #f0f0f0;
}

.metadata-line {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
  font-size: 0.9rem;
  color: #666;
}

.label {
  font-weight: 500;
}

.year {
  color: #999;
}

.year::before {
  content: '•';
  margin-right: 0.5rem;
}

/* Track Count Comparison */
.track-comparison {
  font-size: 0.9rem;
  font-weight: 600;
  padding: 0.25rem 0.75rem;
  border-radius: 12px;
  display: inline-block;
  margin-bottom: 0.75rem;
}

.track-comparison.perfect-match {
  background: #d4edda;
  color: #155724;
}

.track-comparison.bonus-content {
  background: #cce7ff;
  color: #0066cc;
}

.track-comparison.minor-incomplete {
  background: #fff3cd;
  color: #856404;
}

.track-comparison.major-discrepancy {
  background: #f8d7da;
  color: #721c24;
}

.track-count {
  font-size: 0.85rem;
  color: #666;
  margin-bottom: 0.75rem;
}

/* Actions */
.actions {
  display: flex;
  gap: 0.5rem;
}

.btn-youtube {
  padding: 0.75rem 1rem;
  background: #ff0000;
  color: white;
  text-decoration: none;
  border-radius: 4px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
  text-align: center;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 80px;
}

.btn-youtube:hover {
  background: #cc0000;
  transform: translateY(-1px);
  text-decoration: none;
  color: white;
}

.btn-download {
  flex: 1;
  padding: 0.75rem;
  background: #42b883;
  color: white;
  border: none;
  border-radius: 4px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s ease;
}

.btn-download:hover:not(:disabled) {
  background: #35a271;
  transform: translateY(-1px);
}

.btn-download:disabled {
  background: #ccc;
  cursor: not-allowed;
  transform: none;
}

/* Responsive Design */
@media (max-width: 768px) {
  .card-header {
    padding: 0.75rem 0.75rem 1.25rem 0.75rem; /* Added extra bottom padding */
    height: 6.5rem; /* Increased by 0.5rem */
  }
  
  /* Image stays square on mobile - aspect-ratio handles this automatically */
  
  .card-footer {
    padding: 0.75rem;
  }
  
  /* Adjust text sizes for smaller cards */
  .artist {
    font-size: 0.9rem;
  }
  
  .title {
    font-size: 1rem;
  }
}
</style>