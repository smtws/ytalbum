<template>
  <div class="search-bar">
    <h1>YouTube Music Downloader</h1>
    <form @submit.prevent="handleSubmit" class="search-form">
      <input
        ref="searchInput"
        v-model="query"
        type="text"
        placeholder="Search for an artist..."
        class="search-input"
        :disabled="disabled"
        @keydown.enter="handleSubmit"
      />
      <button
        type="submit"
        class="search-button"
        :disabled="disabled || !query.trim()"
      >
        Search
      </button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';

interface Props {
  disabled?: boolean;
}

interface Emits {
  (e: 'search', query: string): void;
}

defineProps<Props>();
const emit = defineEmits<Emits>();

const query = ref('');
const searchInput = ref<HTMLInputElement>();

const handleSubmit = () => {
  if (query.value.trim()) {
    emit('search', query.value.trim());
  }
};

onMounted(() => {
  // Focus the search input on component mount
  if (searchInput.value) {
    searchInput.value.focus();
  }
});
</script>

<style scoped>
.search-bar {
  background: white;
  padding: 2rem;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  margin-bottom: 2rem;
}

h1 {
  margin: 0 0 1.5rem;
  color: #2c3e50;
  text-align: center;
  font-size: 2rem;
}

.search-form {
  display: flex;
  gap: 1rem;
}

.search-input {
  flex: 1;
  padding: 0.75rem 1rem;
  font-size: 1rem;
  border: 2px solid #ddd;
  border-radius: 4px;
  transition: border-color 0.3s;
}

.search-input:focus {
  outline: none;
  border-color: #42b883;
}

.search-input:disabled {
  background: #f5f5f5;
  cursor: not-allowed;
}

.search-button {
  padding: 0.75rem 2rem;
  font-size: 1rem;
  font-weight: 600;
  color: white;
  background: #42b883;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.3s;
}

.search-button:hover:not(:disabled) {
  background: #35a271;
  transform: translateY(-1px);
}

.search-button:disabled {
  background: #ccc;
  cursor: not-allowed;
}

</style>