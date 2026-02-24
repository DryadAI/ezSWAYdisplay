#!/usr/bin/env python3
import sys
import os

# Add current directory to path so we can import ezsway
sys.path.append(os.getcwd())

from ezsway.core.wm_adapter import WMFactory

def main():
    try:
        adapter = WMFactory.create_adapter()
        print(f"Using adapter: {type(adapter).__name__}")
        
        monitors = adapter.get_outputs()
        print(f"Found {len(monitors)} monitors:")
        for m in monitors:
            print(f" - {m.name}: {m.make} {m.model} ({m.unique_id}) Active={m.active}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
