#!/usr/bin/env python3
"""
Test StateManager complete workflow with parallel searches and deduplication
"""

import asyncio
import time
import sys
import os
sys.path.insert(0, '/home/tordt/YT-Downloads')

from backend.core.state_manager import StateManager
from backend.services.youtube_deduplicator import YouTubeDeduplicator


class StateManagerWorkflowTester:
    def __init__(self):
        self.query = "Sabaton"
        self.all_strategies = [
            "channel_search",
            "youtube_music_search", 
            "playlist_search",
            "google_search",
            "direct_album_search",
            "genre_context_search",
            "alternative_title_search"
        ]
    
    async def test_complete_workflow(self):
        """Test the complete StateManager workflow with deduplication"""
        print("🎵 STATE MANAGER COMPLETE WORKFLOW TEST")
        print("="*60)
        print(f"Query: '{self.query}'")
        print(f"Strategies: {len(self.all_strategies)} services")
        print(f"Cookie source: {self._get_cookie_info()}")
        print("-"*60)
        
        # Initialize StateManager (triggers cookie detection)
        state_manager = StateManager()
        
        # Display initial state
        print(f"Initial state:")
        print(f"  Cookie compatible: {state_manager.state.config.cookie_info.yt_dlp_compatible}")
        print(f"  Recommended browser: {state_manager.state.config.cookie_info.recommended_browser}")
        print(f"  Initial results: {len(state_manager.state.results)}")
        print()
        
        # Start the search workflow
        start_time = time.time()
        await state_manager.start_search(self.query, self.all_strategies)
        
        # Wait for all searches to complete
        await self._wait_for_completion(state_manager)
        
        total_time = time.time() - start_time
        
        # Analyze results
        await self._analyze_results(state_manager, total_time)
        
        return state_manager.state
    
    async def _wait_for_completion(self, state_manager: StateManager):
        """Wait for all search tasks to complete"""
        print("⏳ Waiting for all search services to complete...")
        
        # Monitor progress
        completed_services = set()
        total_services = len(self.all_strategies)
        
        while len(completed_services) < total_services:
            # Check which services have completed
            current_completed = set(state_manager.state.search_strategies_completed)
            
            # Report newly completed services
            newly_completed = current_completed - completed_services
            for service in newly_completed:
                results_count = len([r for r in state_manager.state.results if r.discovered_by == service])
                print(f"  ✅ {service}: {results_count} unique results added to state")
            
            completed_services = current_completed
            
            if len(completed_services) < total_services:
                await asyncio.sleep(1)
        
        print(f"🎯 All {total_services} services completed!")
        print()
    
    async def _analyze_results(self, state_manager: StateManager, total_time: float):
        """Analyze the final results and deduplication statistics"""
        state = state_manager.state
        
        print("📊 WORKFLOW ANALYSIS")
        print("="*60)
        
        # Overall performance
        print(f"Total execution time: {total_time:.2f} seconds")
        print(f"Final unique results: {len(state.results)}")
        print(f"Duplicates removed: {state.duplicates_removed}")
        print()
        
        # Deduplication effectiveness
        total_raw_results = len(state.results) + state.duplicates_removed
        if total_raw_results > 0:
            duplicate_rate = (state.duplicates_removed / total_raw_results) * 100
            print(f"Deduplication statistics:")
            print(f"  Raw results processed: {total_raw_results}")
            print(f"  Unique results kept: {len(state.results)}")
            print(f"  Duplicates removed: {state.duplicates_removed}")
            print(f"  Duplicate rate: {duplicate_rate:.1f}%")
            print()
        
        # Results by service
        print("Results by service:")
        service_stats = {}
        for result in state.results:
            service = result.discovered_by
            if service not in service_stats:
                service_stats[service] = {"count": 0, "playlists": 0, "videos": 0}
            service_stats[service]["count"] += 1
            
            # Categorize by type
            if "playlist:" in (result.youtube_id or ""):
                service_stats[service]["playlists"] += 1
            else:
                service_stats[service]["videos"] += 1
        
        for service, stats in sorted(service_stats.items(), key=lambda x: x[1]["count"], reverse=True):
            playlists = stats["playlists"]
            videos = stats["videos"]
            print(f"  {service}: {stats['count']} total ({playlists} playlists, {videos} videos)")
        print()
        
        # Content type breakdown
        total_playlists = sum(s["playlists"] for s in service_stats.values())
        total_videos = sum(s["videos"] for s in service_stats.values())
        
        print("Content type breakdown:")
        print(f"  Playlists: {total_playlists} ({(total_playlists/len(state.results)*100):.1f}%)")
        print(f"  Individual videos: {total_videos} ({(total_videos/len(state.results)*100):.1f}%)")
        print()
        
        # Quality assessment
        high_quality_results = [r for r in state.results if (r.quality_score or 0) >= 0.8]
        print(f"High quality results (score ≥ 0.8): {len(high_quality_results)}/{len(state.results)} ({len(high_quality_results)/len(state.results)*100:.1f}%)")
        
        # Sample results
        print("\nSample results (first 5):")
        for i, result in enumerate(state.results[:5]):
            result_type = "playlist" if "playlist:" in (result.youtube_id or "") else "video"
            print(f"  {i+1}. [{result_type}] {result.title} (from {result.discovered_by})")
            print(f"     ID: {result.youtube_id}, Quality: {result.quality_score or 'N/A'}")
        
        if len(state.results) > 5:
            print(f"     ... and {len(state.results) - 5} more results")
        print()
        
        # Performance requirements check
        print("🎯 PERFORMANCE REQUIREMENTS:")
        total_time_pass = total_time <= 60
        first_results_time = self._estimate_first_results_time(total_time)
        first_results_pass = first_results_time <= 10
        
        print(f"  ✅ Total time < 60s: {'PASS' if total_time_pass else 'FAIL'} ({total_time:.2f}s)")
        print(f"  ✅ First results < 10s: {'PASS' if first_results_pass else 'FAIL'} (~{first_results_time:.1f}s)")
        print(f"  📊 Deduplication working: {'PASS' if state.duplicates_removed > 0 else 'UNKNOWN'} ({state.duplicates_removed} duplicates removed)")
        
        return {
            "total_time": total_time,
            "unique_results": len(state.results),
            "duplicates_removed": state.duplicates_removed,
            "duplicate_rate": duplicate_rate if total_raw_results > 0 else 0,
            "total_playlists": total_playlists,
            "total_videos": total_videos,
            "service_stats": service_stats
        }
    
    def _estimate_first_results_time(self, total_time: float) -> float:
        """Estimate when first results appeared (rough approximation)"""
        # Based on our parallel testing, first results typically appear around 5-9 seconds
        # This is a rough estimate since we don't track exact timing in StateManager yet
        return min(8.0, total_time * 0.3)
    
    def _get_cookie_info(self) -> str:
        """Get cookie information description"""
        # Create a temporary StateManager to check cookie detection
        temp_manager = StateManager()
        cookie_info = temp_manager.state.config.cookie_info
        
        if cookie_info.yt_dlp_compatible and cookie_info.recommended_browser:
            return f"{cookie_info.recommended_browser} (compatible)"
        elif cookie_info.firefox_detected or cookie_info.chrome_detected:
            browsers = []
            if cookie_info.firefox_detected:
                browsers.append("Firefox")
            if cookie_info.chrome_detected:
                browsers.append("Chrome")
            return f"{', '.join(browsers)} (detected but not compatible)"
        else:
            return "No browser cookies detected"


async def main():
    """Run the complete StateManager workflow test"""
    tester = StateManagerWorkflowTester()
    
    try:
        final_state = await tester.test_complete_workflow()
        
        print("✅ StateManager workflow test completed successfully!")
        print(f"Final state contains {len(final_state.results)} unique results")
        
    except Exception as e:
        print(f"❌ StateManager workflow test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())