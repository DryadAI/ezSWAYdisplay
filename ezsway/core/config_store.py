import json
import os
from pathlib import Path
from typing import Dict, Optional, Any

class ConfigStore:
    """Manages persistence of monitor configurations."""
    
    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            self.config_dir = Path.home() / ".config" / "ezSWAYdisplay"
        else:
            self.config_dir = config_dir
            
        self.config_file = self.config_dir / "monitors.json"
        self.monitors_db: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        """Loads the configuration from disk."""
        if not self.config_file.exists():
            self.monitors_db = {}
            return

        try:
            with open(self.config_file, 'r') as f:
                self.monitors_db = json.load(f)
        except json.JSONDecodeError:
            # TODO: Backup corrupted file?
            self.monitors_db = {}
        except Exception as e:
            print(f"Failed to load config: {e}")
            self.monitors_db = {}

    def save(self):
        """Saves current configuration to disk."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.monitors_db, f, indent=4)
        except Exception as e:
            print(f"Failed to save config: {e}")

    def get_monitor_config(self, unique_id: str) -> Optional[Dict[str, Any]]:
        """Returns configuration for a specific monitor ID."""
        return self.monitors_db.get(unique_id)

    def set_monitor_config(self, unique_id: str, config: Dict[str, Any]):
        """Sets configuration for a monitor ID."""
        self.monitors_db[unique_id] = config
        self.save()

    def is_known(self, unique_id: str) -> bool:
        """Checks if a monitor ID is known."""
        return unique_id in self.monitors_db

    def forget_monitor(self, unique_id: str):
        """Removes a monitor from the database."""
        if unique_id in self.monitors_db:
            del self.monitors_db[unique_id]
            self.save()
