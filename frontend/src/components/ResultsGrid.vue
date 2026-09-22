<template>
  <div class="results-container">
    <div class="results-header">
      <h2>Search Results ({{ results.length }})</h2>
      <button 
        v-if="results.length > 0"
        @click="$emit('download-all')"
        class="download-all-btn"
      >
        Download All
      </button>
    </div>
    
    <div class="results-grid">
      <ResultCard 
        v-for="result in results" 
        :key="result.id"
        :result="result"
        @download="$emit('download', result.id)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Result } from '../types/AppState';
import ResultCard from './ResultCard.vue';

interface Props {
  results: Result[];
}

interface Emits {
  (e: 'download', resultId: string): void;
  (e: 'download-all'): void;
}

defineProps<Props>();
defineEmits<Emits>();
</script>

<style scoped>
.results-container {
  background: white;
  border-radius: 8px;
  padding: 1.5rem;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.results-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.5rem;
  padding-bottom: 1rem;
  border-bottom: 2px solid #f0f0f0;
}

.results-header h2 {
  margin: 0;
  color: #2c3e50;
}

.download-all-btn {
  padding: 0.5rem 1.5rem;
  background: #42b883;
  color: white;
  border: none;
  border-radius: 4px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s;
}

.download-all-btn:hover {
  background: #35a271;
  transform: translateY(-1px);
}

.results-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.5rem;
  align-items: start;
}

@media (min-width: 1200px) {
  .results-grid {
    grid-template-columns: repeat(4, 1fr);
  }
}

@media (max-width: 1024px) {
  .results-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 768px) {
  .results-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 1rem;
  }
}

@media (max-width: 480px) {
  .results-grid {
    grid-template-columns: 1fr;
  }
}
</style>