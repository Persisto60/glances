#!/usr/bin/env python3
"""
Test script for glances useridle plugin - Continuous monitoring
Works on both Linux and Windows
Press Ctrl+C to interrupt
"""

import time
import signal
import sys

def signal_handler(sig, frame):
    print("\nInterrupted by user. Exiting...")
    sys.exit(0)

# Set up signal handler for Ctrl+C
signal.signal(signal.SIGINT, signal_handler)

try:
    from glances.plugins.useridle import Plugin
    print("Successfully imported useridle plugin")
    
    # Create plugin instance
    p = Plugin()
    print("Plugin created successfully")
    print("Starting continuous monitoring... (Press Ctrl+C to stop)")
    print("-" * 50)
    
    loop_count = 0
    while True:
        loop_count += 1
        print(f"\n[Loop {loop_count}] {time.strftime('%H:%M:%S')}")
        
        # Update stats
        try:
            stats = p.update()
            print(f"Stats: {stats}")
            
            # Show idle time if available
            if stats and 'idle' in stats:
                idle_seconds = stats['idle']
                idle_minutes = idle_seconds / 60
                print(f"Idle time: {idle_seconds:.1f}s ({idle_minutes:.1f}min)")
            
        except Exception as e:
            print(f"Error updating stats: {e}")
        
        # Get curse output
        try:
            curse = p.msg_curse()
            if curse:
                print(f"Curse output: {curse}")
            else:
                print("Curse output: None")
        except Exception as e:
            print(f"Error getting curse output: {e}")
        
        # Wait before next update
        time.sleep(2)
        
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure glances is installed: pip install glances")
except KeyboardInterrupt:
    print("\nInterrupted by user. Exiting...")
except Exception as e:
    print(f"Unexpected error: {e}")
