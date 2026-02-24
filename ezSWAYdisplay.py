#!/usr/bin/env python3
"""
ezSWAYdisplay - Persist Sway display configuration for Garuda Linux
Captures current display layout and saves to ~/.config/sway/config.d/99-display-layout.conf
"""

import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime
import argparse


def get_current_outputs():
    """Query swaymsg for current output configuration."""
    try:
        result = subprocess.run(
            ["swaymsg", "-t", "get_outputs"],
            capture_output=True,
            text=True,
            check=True
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to query swaymsg: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse swaymsg output: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: swaymsg not found. Are you running Sway?", file=sys.stderr)
        sys.exit(1)


def generate_output_config(outputs):
    """Generate Sway output configuration lines from output data."""
    config_lines = []
    
    for output in outputs:
        name = output.get("name")
        active = output.get("active", False)
        
        if not active:
            config_lines.append(f"output {name} disable")
            continue
        
        # Extract current mode
        current_mode = output.get("current_mode", {})
        width = current_mode.get("width", 0)
        height = current_mode.get("height", 0)
        refresh = current_mode.get("refresh", 0)
        refresh_hz = refresh / 1000.0  # Convert mHz to Hz
        
        # Extract position
        rect = output.get("rect", {})
        pos_x = rect.get("x", 0)
        pos_y = rect.get("y", 0)
        
        # Extract scale
        scale = output.get("scale", 1.0)
        
        # Build output line
        config_line = f"output {name} mode {width}x{height}@{refresh_hz:.3f}Hz pos {pos_x} {pos_y} scale {scale}"
        config_lines.append(config_line)
    
    return config_lines


def create_config_file(config_lines, config_path, dry_run=False):
    """Write configuration to file with header."""
    header = f"""# Auto-generated display configuration for Sway
# Created by ezSWAYdisplay on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# DO NOT EDIT MANUALLY - This file will be overwritten
# To update: Run ezSWAYdisplay.py again

"""
    
    content = header + "\n".join(config_lines) + "\n"
    
    if dry_run:
        print("=== DRY RUN: Would write the following to", config_path, "===")
        print(content)
        return
    
    try:
        config_path.write_text(content)
        print(f"✓ Configuration written to {config_path}")
    except IOError as e:
        print(f"Error: Failed to write config file: {e}", file=sys.stderr)
        sys.exit(1)


def backup_existing_config(config_path):
    """Create a backup of existing config file if it exists."""
    if not config_path.exists():
        return None
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = config_path.with_suffix(f'.conf.backup_{timestamp}')
    
    try:
        backup_path.write_text(config_path.read_text())
        print(f"✓ Backup created: {backup_path}")
        return backup_path
    except IOError as e:
        print(f"Warning: Failed to create backup: {e}", file=sys.stderr)
        return None


def reload_sway():
    """Reload Sway configuration."""
    try:
        subprocess.run(["swaymsg", "reload"], check=True, capture_output=True)
        print("✓ Sway configuration reloaded")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to reload Sway: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Persist current Sway display configuration to config file"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be written without actually writing"
    )
    parser.add_argument(
        "--no-reload", "-R",
        action="store_true",
        help="Don't reload Sway after writing config"
    )
    parser.add_argument(
        "--no-backup", "-B",
        action="store_true",
        help="Don't create backup of existing config"
    )
    
    args = parser.parse_args()
    
    # Define paths
    config_dir = Path.home() / ".config" / "sway" / "config.d"
    config_file = config_dir / "99-display-layout.conf"
    
    # Ensure config directory exists
    if not args.dry_run:
        config_dir.mkdir(parents=True, exist_ok=True)
        print(f"✓ Config directory ready: {config_dir}")
    
    # Get current outputs
    print("Querying current display configuration...")
    outputs = get_current_outputs()
    print(f"✓ Found {len(outputs)} output(s)")
    
    # Generate config
    config_lines = generate_output_config(outputs)
    
    # Backup existing config
    if not args.no_backup and not args.dry_run:
        backup_existing_config(config_file)
    
    # Write config
    create_config_file(config_lines, config_file, dry_run=args.dry_run)
    
    # Reload Sway
    if not args.no_reload and not args.dry_run:
        reload_sway()
    
    print("\n✓ Done! Your display configuration has been persisted.")


if __name__ == "__main__":
    main()

