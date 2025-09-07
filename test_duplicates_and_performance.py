#!/usr/bin/env python3
"""
Analyze duplicates and performance issues in search services
"""

import asyncio
import time
import sys
import os
sys.path.insert(0, '/home/tordt/YT-Downloads')

from backend.services.search.channel_search import ChannelSearchService
from backend.services.search.genre_context_search import GenreContextSearchService
from backend.services.search.playlist_search import PlaylistSearchService
from backend.services.youtube_deduplicator import YouTubeDeduplicator


class DuplicateAnalyzer:
    def __init__(self):
        self.cookie_options = {"cookiesfrombrowser": ("firefox", None, None, None)}
        self.search_query = "Sabaton"
    
    async def analyze_channel_search(self):
        """Analyze channel_search for duplicates"""
        print("\n" + "="*60)
        print("ANALYZING CHANNEL_SEARCH")
        print("="*60)
        
        service = ChannelSearchService()
        start_time = time.time()
        results = await service.search(self.search_query, cookie_options=self.cookie_options)
        total_time = time.time() - start_time
        
        # Analyze duplicates
        video_ids = []
        urls = []
        titles = []
        duplicate_ids = []
        duplicate_urls = []
        duplicate_titles = []
        
        for result in results:
            # Check video IDs
            if result.youtube_id:
                if result.youtube_id in video_ids:
                    duplicate_ids.append(result.youtube_id)
                else:
                    video_ids.append(result.youtube_id)
            
            # Check URLs
            if result.youtube_url in urls:
                duplicate_urls.append(result.youtube_url)
            else:
                urls.append(result.youtube_url)
            
            # Check titles
            if result.title in titles:
                duplicate_titles.append(result.title)
            else:
                titles.append(result.title)
        
        print(f"Total Results: {len(results)}")
        print(f"Unique Video IDs: {len(video_ids)}")
        print(f"Unique URLs: {len(urls)}")
        print(f"Unique Titles: {len(titles)}")
        print(f"Duplicate IDs: {len(duplicate_ids)}")
        print(f"Duplicate URLs: {len(duplicate_urls)}")
        print(f"Duplicate Titles: {len(duplicate_titles)}")
        print(f"Total Time: {total_time:.2f}s")
        
        if duplicate_urls:
            print(f"\nSample Duplicate URLs (first 5):")
            for url in duplicate_urls[:5]:
                print(f"  - {url}")
        
        return {
            "service": "channel_search",
            "total": len(results),
            "unique_urls": len(urls),
            "duplicate_urls": len(duplicate_urls),
            "time": total_time
        }
    
    async def analyze_genre_context_search(self):
        """Analyze genre_context_search for duplicates"""
        print("\n" + "="*60)
        print("ANALYZING GENRE_CONTEXT_SEARCH")
        print("="*60)
        
        service = GenreContextSearchService()
        start_time = time.time()
        results = await service.search(self.search_query, cookie_options=self.cookie_options)
        total_time = time.time() - start_time
        
        # Analyze duplicates
        urls = []
        titles = []
        duplicate_urls = []
        duplicate_titles = []
        
        for result in results:
            # Check URLs
            if result.youtube_url in urls:
                duplicate_urls.append(result.youtube_url)
            else:
                urls.append(result.youtube_url)
            
            # Check titles
            if result.title in titles:
                duplicate_titles.append(result.title)
            else:
                titles.append(result.title)
        
        print(f"Total Results: {len(results)}")
        print(f"Unique URLs: {len(urls)}")
        print(f"Unique Titles: {len(titles)}")
        print(f"Duplicate URLs: {len(duplicate_urls)}")
        print(f"Duplicate Titles: {len(duplicate_titles)}")
        print(f"Total Time: {total_time:.2f}s")
        
        if duplicate_urls:
            print(f"\nSample Duplicate URLs (first 5):")
            for url in duplicate_urls[:5]:
                print(f"  - {url}")
        
        return {
            "service": "genre_context_search",
            "total": len(results),
            "unique_urls": len(urls),
            "duplicate_urls": len(duplicate_urls),
            "time": total_time
        }
    
    async def analyze_playlist_search_performance(self):
        """Analyze why playlist_search is slow"""
        print("\n" + "="*60)
        print("ANALYZING PLAYLIST_SEARCH PERFORMANCE")
        print("="*60)
        
        service = PlaylistSearchService()
        
        # Time each strategy separately
        strategies = [
            ("_search_official_playlists", service._search_official_playlists),
            ("_search_user_album_playlists", service._search_user_album_playlists),
            ("_search_label_playlists", service._search_label_playlists)
        ]
        
        for strategy_name, strategy_method in strategies:
            start = time.time()
            results = await strategy_method(self.search_query, None, self.cookie_options)
            elapsed = time.time() - start
            print(f"{strategy_name}: {len(results)} results in {elapsed:.2f}s")
            
            # Check for duplicates within this strategy
            urls = []
            duplicates = []
            for r in results:
                if r.youtube_url in urls:
                    duplicates.append(r.youtube_url)
                else:
                    urls.append(r.youtube_url)
            
            if duplicates:
                print(f"  - {len(duplicates)} duplicates within this strategy")
    
    async def test_deduplicator(self):
        """Test if YouTubeDeduplicator is working properly"""
        print("\n" + "="*60)
        print("TESTING DEDUPLICATOR")
        print("="*60)
        
        # Create some test results with duplicates
        test_urls = [
            "https://www.youtube.com/watch?v=abc123",
            "https://www.youtube.com/watch?v=abc123&list=playlist1",
            "https://www.youtube.com/watch?v=abc123&t=10s",
            "https://www.youtube.com/watch?v=def456",
            "https://www.youtube.com/watch?v=def456&list=playlist2"
        ]
        
        for url in test_urls:
            result = YouTubeDeduplicator.prepare_result(
                url=url,
                title=f"Test Video {url[-6:]}",
                artist="Test Artist",
                channel="Test Channel",
                discovered_by="test_service"
            )
            if result:
                print(f"URL: {url}")
                print(f"  -> Video ID: {result.youtube_id}")
                print(f"  -> Normalized URL: {result.youtube_url}")
    
    async def run_all_tests(self):
        """Run all analysis tests"""
        print("🔍 DUPLICATE AND PERFORMANCE ANALYSIS")
        print(f"Query: '{self.search_query}'")
        print(f"Cookie Source: Firefox")
        
        # Analyze services with high counts
        channel_results = await self.analyze_channel_search()
        genre_results = await self.analyze_genre_context_search()
        
        # Analyze slow playlist search
        await self.analyze_playlist_search_performance()
        
        # Test deduplicator
        await self.test_deduplicator()
        
        # Summary
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)
        
        print(f"\nDuplicate Analysis:")
        print(f"channel_search: {channel_results['total']} total, {channel_results['unique_urls']} unique ({channel_results['duplicate_urls']} duplicates)")
        print(f"genre_context_search: {genre_results['total']} total, {genre_results['unique_urls']} unique ({genre_results['duplicate_urls']} duplicates)")
        
        duplicate_ratio_channel = (channel_results['duplicate_urls'] / channel_results['total'] * 100) if channel_results['total'] > 0 else 0
        duplicate_ratio_genre = (genre_results['duplicate_urls'] / genre_results['total'] * 100) if genre_results['total'] > 0 else 0
        
        print(f"\nDuplicate Ratios:")
        print(f"channel_search: {duplicate_ratio_channel:.1f}% duplicates")
        print(f"genre_context_search: {duplicate_ratio_genre:.1f}% duplicates")


async def main():
    analyzer = DuplicateAnalyzer()
    await analyzer.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())