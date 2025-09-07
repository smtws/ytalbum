#!/usr/bin/env python3
"""
Test parallel execution of all search services
"""

import asyncio
import time
import sys
import os
sys.path.insert(0, '/home/tordt/YT-Downloads')

from backend.services.search.channel_search import ChannelSearchService
from backend.services.search.youtube_music_search import YouTubeMusicSearchService
from backend.services.search.playlist_search import PlaylistSearchService
from backend.services.search.google_search import GoogleSearchService
from backend.services.search.direct_album_search import DirectAlbumSearchService
from backend.services.search.genre_context_search import GenreContextSearchService
from backend.services.search.alternative_title_search import AlternativeTitleSearchService


class ParallelSearchTester:
    def __init__(self):
        self.cookie_options = {"cookiesfrombrowser": ("firefox", None, None, None)}
        self.search_query = "Sabaton"
        
        self.services = {
            "channel_search": ChannelSearchService(),
            "youtube_music_search": YouTubeMusicSearchService(),
            "playlist_search": PlaylistSearchService(),
            "google_search": GoogleSearchService(),
            "direct_album_search": DirectAlbumSearchService(),
            "genre_context_search": GenreContextSearchService(),
            "alternative_title_search": AlternativeTitleSearchService()
        }
    
    async def test_service_with_timing(self, service_name, service):
        """Test a single service and track timing milestones"""
        print(f"🚀 {service_name} started")
        start_time = time.time()
        
        try:
            results = await service.search(self.search_query, cookie_options=self.cookie_options)
            end_time = time.time()
            total_time = end_time - start_time
            
            print(f"✅ {service_name} completed: {len(results)} results in {total_time:.2f}s")
            
            return {
                "service": service_name,
                "results": len(results),
                "time": total_time,
                "status": "success"
            }
            
        except Exception as e:
            end_time = time.time()
            total_time = end_time - start_time
            
            print(f"❌ {service_name} failed: {e} after {total_time:.2f}s")
            
            return {
                "service": service_name,
                "results": 0,
                "time": total_time,
                "status": "failed",
                "error": str(e)
            }
    
    async def run_parallel_test(self):
        """Run all services in parallel and track timing"""
        print("🎵 PARALLEL SEARCH PERFORMANCE TEST")
        print(f"Query: '{self.search_query}'")
        print(f"Cookie Source: Firefox")
        print(f"Services: {len(self.services)} running in parallel")
        print("-" * 60)
        
        start_time = time.time()
        
        # Create tasks for all services
        tasks = []
        for service_name, service in self.services.items():
            task = self.test_service_with_timing(service_name, service)
            tasks.append(task)
        
        # Run all in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        end_time = time.time()
        total_parallel_time = end_time - start_time
        
        print("\n" + "="*60)
        print("PARALLEL EXECUTION RESULTS")
        print("="*60)
        
        # Process results
        successful_results = []
        failed_results = []
        
        for result in results:
            if isinstance(result, dict):
                if result.get("status") == "success":
                    successful_results.append(result)
                else:
                    failed_results.append(result)
            else:
                # Exception occurred
                print(f"❌ Exception: {result}")
        
        # Sort by completion time
        successful_results.sort(key=lambda x: x["time"])
        
        print(f"Total Parallel Time: {total_parallel_time:.2f} seconds")
        print(f"Successful Services: {len(successful_results)}")
        print(f"Failed Services: {len(failed_results)}")
        
        if successful_results:
            first_service = successful_results[0]
            print(f"First to Complete: {first_service['service']} ({first_service['time']:.2f}s)")
            
            last_service = successful_results[-1]
            print(f"Last to Complete: {last_service['service']} ({last_service['time']:.2f}s)")
        
        print(f"\nCompletion Order:")
        for i, result in enumerate(successful_results, 1):
            print(f"  {i}. {result['service']}: {result['results']} results in {result['time']:.2f}s")
        
        if failed_results:
            print(f"\nFailed Services:")
            for result in failed_results:
                print(f"  ❌ {result['service']}: {result.get('error', 'Unknown error')} after {result['time']:.2f}s")
        
        # Performance analysis
        print("\n" + "="*60)
        print("PERFORMANCE ANALYSIS")
        print("="*60)
        
        total_results = sum(r["results"] for r in successful_results)
        print(f"Total Results Found: {total_results}")
        
        if successful_results:
            time_to_first_results = successful_results[0]["time"]
            print(f"Time to First Results: {time_to_first_results:.2f}s")
            
            # Check if we meet requirements
            meets_first_results = time_to_first_results <= 10
            meets_total_time = total_parallel_time <= 60
            
            print(f"\n🎯 REQUIREMENTS CHECK:")
            print(f"  ✅ First results < 10s: {'PASS' if meets_first_results else 'FAIL'} ({time_to_first_results:.2f}s)")
            print(f"  ✅ Total time < 60s: {'PASS' if meets_total_time else 'FAIL'} ({total_parallel_time:.2f}s)")
            
            # Calculate result flow
            if len(successful_results) > 1:
                gaps = []
                for i in range(1, len(successful_results)):
                    gap = successful_results[i]["time"] - successful_results[i-1]["time"]
                    gaps.append(gap)
                
                max_gap = max(gaps) if gaps else 0
                avg_gap = sum(gaps) / len(gaps) if gaps else 0
                
                meets_gap_requirement = max_gap <= 2.0
                print(f"  ✅ Max gap < 2s: {'PASS' if meets_gap_requirement else 'FAIL'} ({max_gap:.2f}s)")
                print(f"  📊 Average gap: {avg_gap:.2f}s")
                
                print(f"\nResult Flow Timeline:")
                cumulative_results = 0
                for result in successful_results:
                    cumulative_results += result["results"]
                    print(f"  {result['time']:.1f}s: +{result['results']} results (total: {cumulative_results})")
        
        return {
            "total_time": total_parallel_time,
            "first_results_time": successful_results[0]["time"] if successful_results else None,
            "total_results": total_results,
            "successful_services": len(successful_results),
            "results": successful_results
        }


async def main():
    tester = ParallelSearchTester()
    await tester.run_parallel_test()


if __name__ == "__main__":
    asyncio.run(main())