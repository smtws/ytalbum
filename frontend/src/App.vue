<template>
  <div id="app">
    <!-- Connection Status Bar -->
    <div class="status-bar" :class="statusClass">
      <div class="status-content">
        <span class="status-icon">{{ statusIcon }}</span>
        <span class="status-text">{{ statusText }}</span>
        <span v-if="appState?.current?.status" class="app-status">| {{ appState.current.status.toUpperCase() }}</span>
      </div>
    </div>

    <!-- Main Content -->
    <div class="container">
      <!-- Search Section -->
      <SearchBar @search="handleSearch" :disabled="!isConnected" />
      
      <!-- Debug Info -->
      <div style="background: #f0f0f0; padding: 1rem; margin: 1rem 0; border-radius: 4px;">
        <h3>Debug Info:</h3>
        <p>Connected: {{ isConnected }}</p>
        <p>Error: {{ connectionError }}</p>
        <p>AppState: {{ appState ? 'Present' : 'Null' }}</p>
        <p>Results: {{ appState?.results?.length || 0 }}</p>
        <button @click="testSearch" style="margin-right: 1rem;">Test Search</button>
        <button @click="wsService.testDummyData()">Load Test Data</button>
      </div>
      
      <!-- Current Activity -->
      <ActivityPanel v-if="appState?.current" :currentState="appState.current" :totals="appState.totals" :resultCount="appState.results?.length" />
      
            <!-- Results Summary -->

      <div v-if="appState?.results?.length" class="results-summary">
        <h3>Search Results: {{ appState.results.length }}</h3>
        <div class="verification-stats">
          <span class="stat verified">✅ {{ appState.totals.metadata_verified }} Verified</span>
          <span class="stat unverified">❓ {{ appState.totals.metadata_not_found }} Unverified</span>
        </div>
      </div>
      
      <!-- AppState Debug View -->
      <div v-if="appState" class="appstate-debug">
        <h3>AppState Structure (Live Updates)</h3>
        <pre class="json-display">{{ formattedAppState }}</pre>
      </div>
      


      
      <!-- Results Grid (Hidden for now) -->
       <ResultsGrid v-if="appState?.results?.length" :results="appState.results" @download="handleDownload" /> 
      
      <!-- Empty State -->
      <div v-else-if="isConnected && appState" class="empty-state">
        <p>No results yet. Search for an artist to get started.</p>
      </div>
      
      <!-- Connection Error -->
      <div v-else-if="!isConnected" class="error-state">
        <h2>Connecting to backend...</h2>
        <p>{{ connectionError || 'Please ensure the backend is running on port 7002' }}</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, computed, watch } from 'vue';
import { wsService, appState, isConnected, connectionError } from './services/WebSocketService';
import SearchBar from './components/SearchBar.vue';
import ActivityPanel from './components/ActivityPanel.vue';
import ResultsGrid from './components/ResultsGrid.vue';

// Connect to WebSocket on mount
onMounted(() => {
  console.log('App mounted, connecting to WebSocket...');
  wsService.connect();
  
  // Debug log the reactive values
  console.log('Initial state:', {
    connected: isConnected.value,
    appState: appState.value,
    error: connectionError.value
  });
});

// Clear cached results when WebSocket connection is established
let hasCleared = false;
watch(isConnected, (connected) => {
  if (connected && !hasCleared) {
    console.log('WebSocket connected - clearing cached results for fresh data...');
    wsService.clearResults();
    hasCleared = true; // Only clear once per session
  }
});

// Disconnect on unmount
onUnmounted(() => {
  wsService.disconnect();
});

// Computed properties for status display
const statusClass = computed(() => ({
  'connected': isConnected.value,
  'disconnected': !isConnected.value,
  'error': connectionError.value !== null
}));

const statusIcon = computed(() => {
  if (connectionError.value) return '⚠️';
  return isConnected.value ? '🟢' : '🔴';
});

const statusText = computed(() => {
  if (connectionError.value) return connectionError.value;
  return isConnected.value ? 'Connected to Backend' : 'Disconnected';
});

// Command handlers - just pass through to service
const handleSearch = (query: string) => {
  console.log('handleSearch called with:', query);
  wsService.search(query);
};

const handleDownload = (resultId: string) => {
  wsService.download(resultId);
};

const testSearch = () => {
  console.log('Test search triggered');
  wsService.search('sabaton');
};

// Computed properties for verification breakdown
const verifiedCount = computed(() => {
  if (!appState.value?.results) return 0;
  return appState.value.results.filter(r => r.verified === true).length;
});

const unverifiedCount = computed(() => {
  if (!appState.value?.results) return 0;
  return appState.value.results.filter(r => r.verified === false).length;
});

const pendingCount = computed(() => {
  if (!appState.value?.results) return 0;
  return appState.value.results.filter(r => r.verified === null).length;
});

// Formatted JSON for debug view - RAW unfiltered data
const formattedAppState = computed(() => {
  if (!appState.value) return 'null';
  
  // Show completely raw AppState with NO filtering
  return JSON.stringify(appState.value, null, 2);
});
</script>

<style scoped>
#app {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: #2c3e50;
  min-height: 100vh;
  background: #f5f5f5;
}

.status-bar {
  position: sticky;
  top: 0;
  z-index: 1000;
  padding: 0.5rem;
  background: #fff;
  border-bottom: 2px solid #ddd;
  transition: all 0.3s ease;
}

.status-bar.connected {
  border-bottom-color: #42b883;
}

.status-bar.disconnected {
  border-bottom-color: #ff6b6b;
}

.status-bar.error {
  border-bottom-color: #f39c12;
}

.status-content {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
}

.status-icon {
  font-size: 1rem;
}

.app-status {
  margin-left: auto;
  font-weight: 600;
  color: #666;
}

.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem;
}

.empty-state,
.error-state {
  text-align: center;
  padding: 4rem 2rem;
  background: white;
  border-radius: 8px;
  margin-top: 2rem;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.empty-state p,
.error-state p {
  color: #666;
  margin-top: 1rem;
}

.error-state h2 {
  color: #ff6b6b;
}

.results-summary {
  background: white;
  border-radius: 8px;
  padding: 1.5rem;
  margin-bottom: 2rem;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.results-summary h3 {
  margin: 0 0 1rem;
  font-size: 1.3rem;
  color: #2c3e50;
}

.verification-stats {
  display: flex;
  gap: 2rem;
  flex-wrap: wrap;
}

.stat {
  font-weight: 600;
  font-size: 0.95rem;
}

.stat.verified {
  color: #42b883;
}

.stat.unverified {
  color: #f39c12;
}

.stat.pending {
  color: #666;
}

.appstate-debug {
  background: white;
  border-radius: 8px;
  padding: 1.5rem;
  margin-bottom: 2rem;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.appstate-debug h3 {
  margin: 0 0 1rem;
  font-size: 1.3rem;
  color: #2c3e50;
}

.json-display {
  background: #f8f9fa;
  border: 1px solid #e9ecef;
  border-radius: 4px;
  padding: 1rem;
  margin: 0;
  overflow-x: auto;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 0.85rem;
  line-height: 1.4;
  color: #495057;
  max-height: 600px;
  overflow-y: auto;
}

.json-display::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

.json-display::-webkit-scrollbar-track {
  background: #f1f1f1;
}

.json-display::-webkit-scrollbar-thumb {
  background: #c1c1c1;
  border-radius: 4px;
}

.json-display::-webkit-scrollbar-thumb:hover {
  background: #a8a8a8;
}
</style>
