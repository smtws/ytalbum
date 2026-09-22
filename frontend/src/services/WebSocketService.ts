/**
 * WebSocket Service for connecting to the backend
 * Handles all communication with the Python backend
 * 
 * This is a "stupid" service - it only relays data, no business logic
 */

import { ref, readonly } from 'vue';
import type { AppState, WebSocketMessage, MessageType, SearchCommand, DownloadCommand, UpdateConfigCommand } from '../types/AppState';

class WebSocketService {
  private ws: WebSocket | null = null;
  private reconnectTimeout: number | null = null;
  private readonly url: string;
  
  // Reactive state that components can watch
  public appState = ref<AppState | null>(null);
  public connected = ref(false);
  public error = ref<string | null>(null);
  
  constructor() {
    // Backend WebSocket URL - adjust port if needed
    this.url = 'ws://localhost:7002/ws';
  }
  
  /**
   * Connect to the backend WebSocket
   */
  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      console.log('WebSocket already connected');
      return;
    }
    
    console.log('Connecting to WebSocket:', this.url);
    this.error.value = null;
    
    try {
      this.ws = new WebSocket(this.url);
      
      this.ws.onopen = () => {
        console.log('WebSocket connected');
        this.connected.value = true;
        this.error.value = null;
        
        // Clear any reconnect timeout
        if (this.reconnectTimeout) {
          clearTimeout(this.reconnectTimeout);
          this.reconnectTimeout = null;
        }
      };
      
      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.handleMessage(data);
        } catch (err) {
          console.error('Failed to parse WebSocket message:', err);
          this.error.value = 'Failed to parse server message';
        }
      };
      
      this.ws.onerror = (event) => {
        console.error('WebSocket error:', event);
        this.error.value = 'Connection error';
      };
      
      this.ws.onclose = () => {
        console.log('WebSocket disconnected');
        this.connected.value = false;
        this.appState.value = null;
        
        // Auto-reconnect after 3 seconds
        this.reconnectTimeout = window.setTimeout(() => {
          console.log('Attempting to reconnect...');
          this.connect();
        }, 3000);
      };
      
    } catch (err) {
      console.error('Failed to create WebSocket:', err);
      this.error.value = 'Failed to connect';
      this.connected.value = false;
    }
  }
  
  /**
   * Disconnect from the backend
   */
  disconnect(): void {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
    
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    
    this.connected.value = false;
    this.appState.value = null;
  }
  
  /**
   * Handle incoming messages from the backend
   */
  private handleMessage(message: any): void {
    console.log('Received WebSocket message:', message);
    
    // Handle wrapped state update messages
    if (message.type === 'state_update' && message.data) {
      // Extract AppState from the data field
      this.appState.value = message.data as AppState;
      console.log('Updated AppState:', this.appState.value.status, 
                  'Results:', this.appState.value.results?.length || 0);
    } 
    // Handle direct AppState messages (fallback)
    else if ('status' in message && 'session_id' in message) {
      // Direct AppState update
      this.appState.value = message as AppState;
      console.log('Updated AppState (direct):', this.appState.value.status, 
                  'Results:', this.appState.value.results?.length || 0);
    } 
    else {
      console.warn('Unknown message format:', message);
    }
  }
  
  /**
   * Send a command to the backend
   */
  private sendCommand(type: string, data?: any): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.error('WebSocket not connected');
      this.error.value = 'Not connected to server';
      return;
    }
    
    const message = {
      type,
      data: data || {}
    };
    
    try {
      this.ws.send(JSON.stringify(message));
      console.log('Sent command:', type, data);
    } catch (err) {
      console.error('Failed to send command:', err);
      this.error.value = 'Failed to send command';
    }
  }
  
  // Command methods - these just send commands, no logic
  
  /**
   * Start a search
   */
  search(query: string): void {
    const data = {
      query
    };
    this.sendCommand('start_search', data);
  }
  
  /**
   * Download a specific result
   */
  download(resultId: string): void {
    const data = {
      result_id: resultId
    };
    this.sendCommand('download', data);
  }
  
  /**
   * Download all results
   */
  downloadAll(): void {
    this.sendCommand('download_all');
  }
  
  /**
   * Cancel a download
   */
  cancelDownload(resultId: string): void {
    this.sendCommand('cancel_download', { result_id: resultId });
  }
  
  /**
   * Clear the download queue
   */
  clearQueue(): void {
    this.sendCommand('clear_queue');
  }
  
  /**
   * Clear search results
   */
  clearResults(): void {
    this.sendCommand('clear_results');
  }
  
  /**
   * Get current state
   */
  getState(): void {
    this.sendCommand('get_state');
  }
  
  /**
   * Load test data (development)
   */
  testDummyData(): void {
    this.sendCommand('test_dummy_data');
  }
}

// Export a singleton instance
export const wsService = new WebSocketService();

// Export readonly refs for components to use
export const appState = readonly(wsService.appState);
export const isConnected = readonly(wsService.connected);
export const connectionError = readonly(wsService.error);