import contextlib
import fcntl
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any

from .errors import ConcurrentAccessError, ConfigStoreError

logger = logging.getLogger(__name__)


class ConfigStore:
    """Manages persistence of monitor configurations."""

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            self.config_dir = Path.home() / ".config" / "ezSWAYdisplay"
        else:
            self.config_dir = config_dir

        self.config_file = self.config_dir / "monitors.json"
        self.lock_path = self.config_dir / ".monitors.lock"
        self.monitors_db: Dict[str, Dict[str, Any]] = {}
        self._load()

    @contextlib.contextmanager
    def _locked(self):
        """Non-blocking exclusive lock around a mutation -- matches
        ProfileManager's identical pattern, added for the identical problem:
        the GUI's background poller and a separately-invoked `ezswaydisplay
        enforce` (cron/systemd) each hold their own in-memory monitors_db
        snapshot loaded once at __init__. Without this, process A activates
        monitor X and saves; process B, still holding its pre-A snapshot,
        later saves its own change and silently overwrites the *entire*
        file with its stale copy -- monitor X reverts to "unknown" with no
        error, undermining the default-deny safety invariant this tool
        exists to enforce. The lock alone isn't sufficient, though -- see
        set_monitor_config/forget_monitor, which also re-read fresh from
        disk under the lock before mutating, rather than trusting the
        possibly-stale in-memory copy.
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(exist_ok=True)
        with open(self.lock_path, "r+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                raise ConcurrentAccessError(
                    "Another ezSWAYdisplay operation is already in progress "
                    "(monitors lock held). Try again in a moment."
                ) from e
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

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

        Does NOT take the lock itself -- callers that need read-modify-write
        safety (set_monitor_config/forget_monitor) take it around the whole
        _load()+mutate+save() sequence. Calling save() directly (as tests
        do) is fine for the single-process case this was always safe for.
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
        with self._locked():
            self._load()  # fresh read under the lock -- see _locked()'s docstring
            self.monitors_db[unique_id] = config
            self.save()

    def is_known(self, unique_id: str) -> bool:
        """Checks if a monitor ID is known."""
        return unique_id in self.monitors_db

    def forget_monitor(self, unique_id: str):
        """Removes a monitor from the database."""
        with self._locked():
            self._load()
            if unique_id in self.monitors_db:
                del self.monitors_db[unique_id]
                self.save()
