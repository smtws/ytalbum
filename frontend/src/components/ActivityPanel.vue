<template>
  <div class="activity-panel" v-if="currentState">
    <div class="activity-header">
      <h2>Current Activity</h2>
      <div class="activity-timestamp">{{ formatTimestamp(currentState.last_activity_timestamp) }}</div>
    </div>
    
    <div class="activity-content">
      <!-- Last Activity -->
      <div class="activity-message">
        {{ currentState.last_activity }}
      </div>
      
      <!-- Statistics Grid -->
      <div class="progress-grid">
        <!-- Mathematical Flow: Found - Duplicates - Singles = Final Results -->
        <div class="progress-item">
          <span class="progress-label">Total Found</span>
          <span class="progress-value">{{ totals?.found || 0 }}</span>
        </div>
        
        <div class="progress-item">
          <span class="progress-label">Duplicates</span>
          <span class="progress-value duplicates">{{ totals?.duplicates || 0 }}</span>
        </div>
        
        <div class="progress-item">
          <span class="progress-label">Singles Removed</span>
          <span class="progress-value filtered">{{ totals?.singles_removed || 0 }}</span>
        </div>
        
        <div class="progress-item">
          <span class="progress-label">Final Results</span>
          <span class="progress-value verified">{{ totals?.results || 0 }}</span>
        </div>
      </div>
      
      <!-- Active Processes -->
      <div class="process-indicators">
        <span v-if="currentState.status === 'searching'" class="process-badge searching">
          🔍 {{ currentState.status.toUpperCase() }}
        </span>
        <span v-for="strategy in currentState.active_strategies" :key="strategy" class="process-badge strategy">
          🎯 {{ formatStrategy(strategy) }}
        </span>
      </div>
      
      <!-- Strategy Progress -->
      <div v-if="totals?.strategies_total && totals.strategies_total > 0" class="strategy-progress">
        <div class="strategy-bar">
          <div 
            class="strategy-fill" 
            :style="{ width: strategyProgress + '%' }"
          ></div>
        </div>
        <div class="strategy-text">
          {{ totals.strategies_completed }} / {{ totals.strategies_total }} strategies completed
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { CurrentState, Totals } from '../types/AppState';

interface Props {
  currentState: CurrentState;
  totals?: Totals;
  resultCount?: number;
}

const props = defineProps<Props>();


const strategyProgress = computed(() => {
  if (!props.totals?.strategies_total) return 0;
  return (props.totals.strategies_completed / props.totals.strategies_total) * 100;
});

const formatTimestamp = (timestamp: string): string => {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  return date.toLocaleTimeString();
};

const formatStrategy = (strategy: string): string => {
  return strategy
    .replace(/_/g, ' ')
    .replace(/\b\w/g, l => l.toUpperCase());
};
</script>

<style scoped>
.activity-panel {
  background: white;
  border-radius: 8px;
  padding: 1.5rem;
  margin-bottom: 2rem;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.activity-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid #eee;
}

.activity-header h2 {
  margin: 0;
  font-size: 1.2rem;
  color: #2c3e50;
}

.activity-timestamp {
  font-size: 0.85rem;
  color: #999;
}

.activity-message {
  padding: 0.75rem;
  background: #f8f9fa;
  border-radius: 4px;
  margin-bottom: 1rem;
  font-size: 0.95rem;
  color: #495057;
}

.progress-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  gap: 1rem;
  margin-bottom: 1rem;
}

.progress-item {
  text-align: center;
  padding: 0.5rem;
  background: #f8f9fa;
  border-radius: 4px;
}

.progress-label {
  display: block;
  font-size: 0.8rem;
  color: #666;
  margin-bottom: 0.25rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.progress-value {
  display: block;
  font-size: 1.5rem;
  font-weight: 600;
  color: #2c3e50;
}

.progress-value.verified {
  color: #42b883;
}

.progress-value.unverified {
  color: #f39c12;
}

.progress-value.duplicates {
  color: #e74c3c;
}

.progress-value.failed {
  color: #e74c3c;
}

.progress-value.filtered {
  color: #f39c12;
}

.process-indicators {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 1rem;
}

.process-badge {
  padding: 0.25rem 0.75rem;
  border-radius: 20px;
  font-size: 0.85rem;
  font-weight: 500;
  animation: pulse 2s infinite;
}

.process-badge.searching {
  background: #e3f2fd;
  color: #1976d2;
}

.process-badge.normalizing {
  background: #f3e5f5;
  color: #7b1fa2;
}

.process-badge.verifying {
  background: #e8f5e9;
  color: #388e3c;
}

.process-badge.strategy {
  background: #fff3e0;
  color: #f57c00;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}

.strategy-progress {
  margin-top: 1rem;
}

.strategy-bar {
  height: 8px;
  background: #e0e0e0;
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 0.5rem;
}

.strategy-fill {
  height: 100%;
  background: linear-gradient(90deg, #42b883, #35a271);
  transition: width 0.3s ease;
}

.strategy-text {
  font-size: 0.85rem;
  color: #666;
  text-align: center;
}
</style>