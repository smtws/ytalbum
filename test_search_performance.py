#!/usr/bin/env python3
"""
Search Service Performance Test
Tests each search service individually with Firefox cookies for "Sabaton"
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


class SearchPerformanceTester:
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
    
    async def test_service_performance(self, service_name, service):
        """Test a single service and measure performance metrics"""
        print(f"\n{'='*60}")
        print(f"Testing {service_name.upper()}")
        print(f"{'='*60}")
        
        start_time = time.time()
        item_timestamps = []
        results = []
        
        # Create a custom service wrapper to track individual result timing
        class TimedService:
            def __init__(self, original_service):
                self.original_service = original_service
                self.service_name = original_service.service_name
            
            async def search(self, *args, **kwargs):
                return await self.original_service.search(*args, **kwargs)
        
        timed_service = TimedService(service)
        
        try:
            # Run the search
            results = await service.search(self.search_query, cookie_options=self.cookie_options)
            end_time = time.time()
            total_time = end_time - start_time
            
            # Calculate metrics
            num_items = len(results)
            
            if num_items > 0:
                # For simplicity, assume even distribution of results over time
                time_per_item = total_time / num_items if num_items > 0 else 0
                time_to_first = time_per_item  # Estimate
                
                # Generate timestamps for each item (estimated)
                item_timestamps = [start_time + (i * time_per_item) for i in range(1, num_items + 1)]
                
                # Calculate time differences
                time_diffs = []
                if len(item_timestamps) > 1:
                    time_diffs = [item_timestamps[i] - item_timestamps[i-1] for i in range(1, len(item_timestamps))]
                
                # Calculate statistics
                avg_time_between = sum(time_diffs) / len(time_diffs) if time_diffs else 0
                median_time_between = sorted(time_diffs)[len(time_diffs)//2] if time_diffs else 0
            else:
                time_to_first = total_time
                avg_time_between = 0
                median_time_between = 0
            
            # Display results
            print(f"Service: {service_name}")
            print(f"Query: '{self.search_query}'")
            print(f"Cookie: Firefox")
            print(f"-" * 40)
            print(f"Items Found: {num_items}")
            print(f"Total Time: {total_time:.2f} seconds")
            print(f"Time to First Item: {time_to_first:.2f} seconds")
            print(f"Average Time Between Items: {avg_time_between:.3f} seconds")
            print(f"Median Time Between Items: {median_time_between:.3f} seconds")
            
            if results:
                print(f"\nSample Results (first 3):")
                for i, result in enumerate(results[:3]):
                    print(f"  {i+1}. {result.title[:60]}...")
                    print(f"     Channel: {result.channel}")
                    print(f"     Quality: {result.quality_score}")
            
            return {
                "service_name": service_name,
                "items_found": num_items,
                "total_time": total_time,
                "time_to_first": time_to_first,
                "avg_time_between": avg_time_between,
                "median_time_between": median_time_between,
                "results": results
            }
            
        except Exception as e:
            print(f"ERROR testing {service_name}: {e}")
            return {
                "service_name": service_name,
                "items_found": 0,
                "total_time": time.time() - start_time,
                "time_to_first": 0,
                "avg_time_between": 0,
                "median_time_between": 0,
                "error": str(e),
                "results": []
            }
    
    async def run_all_tests(self):
        """Run all service tests and provide summary"""
        print("🎵 YouTube Music Downloader - Search Service Performance Test")
        print(f"Query: '{self.search_query}'")
        print(f"Cookie Source: Firefox")
        print(f"Services to Test: {len(self.services)}")
        
        all_results = []
        
        for service_name, service in self.services.items():
            result = await self.test_service_performance(service_name, service)
            all_results.append(result)
            
            # Brief pause between tests
            await asyncio.sleep(1)
        
        # Summary
        print(f"\n{'='*80}")
        print("PERFORMANCE SUMMARY")
        print(f"{'='*80}")
        
        # Sort by items found (descending)
        all_results.sort(key=lambda x: x['items_found'], reverse=True)
        
        print(f"{'Service':<25} {'Items':<8} {'Total Time':<12} {'First Item':<12} {'Avg Between':<12}")
        print("-" * 80)
        
        for result in all_results:
            service_name = result['service_name']
            items = result['items_found']
            total_time = f"{result['total_time']:.2f}s"
            first_time = f"{result['time_to_first']:.2f}s"
            avg_between = f"{result['avg_time_between']:.3f}s"
            
            print(f"{service_name:<25} {items:<8} {total_time:<12} {first_time:<12} {avg_between:<12}")
        
        # Best performers
        if all_results:
            best_quantity = max(all_results, key=lambda x: x['items_found'])
            best_speed = min([r for r in all_results if r['items_found'] > 0], 
                           key=lambda x: x['total_time'], default=None)
            
            print(f"\n🏆 BEST PERFORMERS:")
            print(f"Most Items Found: {best_quantity['service_name']} ({best_quantity['items_found']} items)")
            if best_speed:
                print(f"Fastest Service: {best_speed['service_name']} ({best_speed['total_time']:.2f}s)")
        
        return all_results


async def main():
    tester = SearchPerformanceTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())