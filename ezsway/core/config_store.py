import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any

from .errors import ConfigStoreError

logger = logging.getLogger(__name__)


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
        """Loads the configuration from disk.

        A corrupted file is preserved (moved aside) rather than silently
        discarded, so a bad write never wipes the user's known-monitor state
        without a trace.
        """
        if not self.config_file.exists():
            self.monitors_db = {}
            return

        try:
            with open(self.config_file, 'r') as f:
                self.monitors_db = json.load(f)
        except json.JSONDecodeError:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            corrupt_path = self.config_file.with_suffix(f'.json.corrupt-{timestamp}')
            try:
                self.config_file.rename(corrupt_path)
                logger.warning(
                    "monitors.json was corrupted; moved aside to %s and starting fresh",
                    corrupt_path,
                )
            except OSError as e:
                logger.warning("monitors.json was corrupted and could not be moved aside: %s", e)
            self.monitors_db = {}
        except Exception as e:
            logger.warning("Failed to load config: %s", e)
            self.monitors_db = {}

    def save(self):
        """Saves current configuration to disk.

        Raises ConfigStoreError on any failure instead of swallowing it --
        callers must not assume persistence succeeded just because this
        returned without an exception previously being the only signal.
        """
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConfigStoreError(f"Cannot create config directory {self.config_dir}: {e}") from e

        tmp_file = self.config_file.with_suffix('.json.tmp')
        try:
            with open(tmp_file, 'w') as f:
                json.dump(self.monitors_db, f, indent=4)
            tmp_file.replace(self.config_file)
        except OSError as e:
            raise ConfigStoreError(f"Failed to save config to {self.config_file}: {e}") from e
        except (TypeError, ValueError) as e:
            # This method's own docstring promises "Raises ConfigStoreError
            # on any failure" -- only OSError was actually caught, so a
            # non-serializable value ever ending up in monitors_db (a
            # TypeError from json.dump) would propagate unwrapped past
            # every `except EzSwayError` handler in the app.
            raise ConfigStoreError(f"Failed to serialize config for {self.config_file}: {e}") from e
        finally:
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass

    def get_monitor_config(self, unique_id: str) -> Optional[Dict[str, Any]]:
        """Returns configuration for a specific monitor ID."""
        return self.monitors_db.get(unique_id)

    def set_monitor_config(self, unique_id: str, config: Dict[str, Any]):
        """Sets configuration for a monitor ID. Raises ConfigStoreError if the save fails."""
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
