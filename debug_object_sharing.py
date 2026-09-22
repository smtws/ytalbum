#!/usr/bin/env python3
"""Debug object sharing in metadata"""

import copy

def test_shallow_copy_issue():
    """Test if shallow copy causes object sharing"""
    
    # Original metadata (like what MusicBrainz returns)
    original_metadata = {
        'id': 'test-id',
        'normalized': {
            'artist': 'Sabaton',
            'title': 'The War To End All Wars', 
            'track_count': 11
        }
    }
    
    print("=== TESTING SHALLOW COPY ===")
    
    # Simulate what happens with metadata.copy()
    result1_metadata = original_metadata.copy()  # Shallow copy
    result2_metadata = original_metadata.copy()  # Shallow copy
    
    print("Before modification:")
    print(f"Result 1 normalized: {result1_metadata['normalized']}")
    print(f"Result 2 normalized: {result2_metadata['normalized']}")
    print(f"Same object? {result1_metadata['normalized'] is result2_metadata['normalized']}")
    
    # Simulate what happens in artist propagation
    result1_metadata['normalized']['artist'] = 'Modified Artist'
    
    print("\nAfter modifying result1:")
    print(f"Result 1 normalized: {result1_metadata['normalized']}")
    print(f"Result 2 normalized: {result2_metadata['normalized']}")
    print("^ BUG: Both results affected!")
    
    print("\n=== TESTING DEEP COPY ===")
    
    # Test with deep copy
    result3_metadata = copy.deepcopy(original_metadata)
    result4_metadata = copy.deepcopy(original_metadata)
    
    print("Before modification (deep copy):")
    print(f"Result 3 normalized: {result3_metadata['normalized']}")
    print(f"Result 4 normalized: {result4_metadata['normalized']}")
    print(f"Same object? {result3_metadata['normalized'] is result4_metadata['normalized']}")
    
    # Modify one
    result3_metadata['normalized']['artist'] = 'Modified Artist Deep Copy'
    
    print("\nAfter modifying result3 (deep copy):")
    print(f"Result 3 normalized: {result3_metadata['normalized']}")
    print(f"Result 4 normalized: {result4_metadata['normalized']}")
    print("^ GOOD: Only result3 affected!")

if __name__ == "__main__":
    test_shallow_copy_issue()