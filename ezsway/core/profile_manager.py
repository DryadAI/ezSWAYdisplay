"""Named display-profile ("saved layout") management.

A profile is a named, saved arrangement of monitors -- keyed by hardware
identity (make-model-serial), not connector name, so it survives port
renumbering and flaky docks. This is a separate concept from
ConfigStore/MonitorManager's per-monitor "known/unknown" authorization
policy; the two are orthogonal and can be used independently.
"""
import contextlib
import fcntl
import json
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .errors import (
    ConcurrentAccessError,
    InvalidLabelError,
    ProfileLockedError,
    ProfileNotFoundError,
    WMCommandError,
)
from .wm_adapter import Monitor, WMAdapter

logger = logging.getLogger(__name__)

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
_LOCK_TIMEOUT_SECONDS = 5
_APPLY_VERIFY_RETRIES = 3
_APPLY_VERIFY_DELAY_SECONDS = 0.3


@dataclass
class LoadResult:
    applied: List[str] = field(default_factory=list)
    skipped_not_connected: List[str] = field(default_factory=list)
    failed: List[Dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def validate_label(label: str) -> str:
    """Raises InvalidLabelError for empty/unsafe labels. Returns the label unchanged if valid.

    A label becomes a filename (profiles/<label>.json) -- unvalidated user
    input here is a path-traversal bug, not just a UX nicety. Only a
    conservative allow-list of characters is accepted.
    """
    if not label or not label.strip():
        raise InvalidLabelError("Profile label cannot be empty.")
    if label in (".", ".."):
        raise InvalidLabelError(f"Profile label {label!r} is not allowed.")
    if "/" in label or "\\" in label or ".." in label:
        raise InvalidLabelError(f"Profile label {label!r} cannot contain path separators.")
    if not _LABEL_RE.match(label):
        raise InvalidLabelError(
            f"Profile label {label!r} may only contain letters, numbers, spaces, "
            "'-', '_', '.' (1-64 characters, must start with a letter/number)."
        )
    return label


class ProfileManager:
    def __init__(self, wm_adapter: WMAdapter, config_dir: Optional[Path] = None):
        self.wm = wm_adapter
        base = config_dir or (Path.home() / ".config" / "ezSWAYdisplay")
        self.profiles_dir = base / "profiles"
        self.backups_dir = self.profiles_dir / ".backups"
        self.lock_path = self.profiles_dir / ".lock"
        self.current_path = self.profiles_dir / ".current"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    # -- locking -----------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self):
        """Non-blocking exclusive lock over the profiles dir for the duration
        of a mutating operation. Fails fast (does not queue/block) so a stuck
        operation can't silently hang a second caller forever -- mirrors this
        user's own 'claim before you touch, exits 1 if held' convention.
        """
        self.lock_path.touch(exist_ok=True)
        with open(self.lock_path, "r+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                raise ConcurrentAccessError(
                    "Another ezSWAYdisplay operation is already in progress "
                    "(profiles lock held). Try again in a moment."
                ) from e
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    # -- internal helpers ----------------------------------------------------

    def _profile_path(self, label: str) -> Path:
        return self.profiles_dir / f"{label}.json"

    def _write_atomic(self, path: Path, data: dict):
        tmp = path.with_name(f".{path.name}.tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            tmp.replace(path)
        finally:
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()

    def _read_profile_raw(self, path: Path) -> Optional[dict]:
        """Reads one profile file, isolating (not discarding) a corrupted one."""
        try:
            with open(path) as f:
                return json.load(f)
        except json.JSONDecodeError:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            corrupt_path = path.with_name(f"{path.name}.corrupt-{timestamp}")
            with contextlib.suppress(OSError):
                path.rename(corrupt_path)
            logger.warning("Profile %s was corrupted; moved aside to %s", path.name, corrupt_path)
            return None
        except OSError as e:
            logger.warning("Failed to read profile %s: %s", path, e)
            return None

    def _load_profile_file(self, label: str) -> dict:
        path = self._profile_path(label)
        if not path.exists():
            raise ProfileNotFoundError(f"No such profile: {label!r}")
        data = self._read_profile_raw(path)
        if data is None:
            raise ProfileNotFoundError(
                f"Profile {label!r} was corrupted and has been moved aside; it no longer exists."
            )
        return data

    def _check_not_locked(self, label: str):
        path = self._profile_path(label)
        if path.exists():
            data = self._read_profile_raw(path)
            if data and data.get("locked"):
                raise ProfileLockedError(f"Profile {label!r} is locked; unlock it first.")

    # -- public API ------------------------------------------------------

    def list_profiles(self) -> List[dict]:
        """Lists all profiles. A corrupted file is isolated and skipped, not
        allowed to break the whole listing."""
        current = self.get_current_label()
        results = []
        for path in sorted(self.profiles_dir.glob("*.json")):
            if path.name.startswith("."):
                continue
            data = self._read_profile_raw(path)
            if data is None:
                continue
            results.append({
                "label": data.get("label", path.stem),
                "locked": bool(data.get("locked", False)),
                "active": data.get("label", path.stem) == current,
                "created": data.get("created"),
                "output_count": len(data.get("outputs", [])),
            })
        return results

    def get_current_label(self) -> Optional[str]:
        if self.current_path.exists():
            with contextlib.suppress(OSError):
                return self.current_path.read_text().strip() or None
        return None

    def save_profile(self, label: str, monitors: List[Monitor]) -> None:
        label = validate_label(label)
        with self._locked():
            self._check_not_locked(label)  # re-check after acquiring the lock (TOCTOU)
            data = {
                "label": label,
                "created": datetime.now().isoformat(timespec="seconds"),
                "locked": False,
                "outputs": [
                    {
                        "unique_id": m.unique_id,
                        "name": m.name,
                        "mode": f"{int(m.width)}x{int(m.height)}@{m.refresh_rate:.3f}Hz",
                        "position": f"{m.pos_x} {m.pos_y}",
                        "scale": m.scale,
                        "transform": m.transform,
                        "active": m.active,
                    }
                    for m in monitors
                ],
            }
            self._write_atomic(self._profile_path(label), data)
            logger.info("Saved profile %r (%d outputs)", label, len(monitors))

    def load_profile(self, label: str) -> LoadResult:
        """Applies a saved profile to the currently connected monitors.

        Matches by unique_id. Entries whose monitor isn't currently connected
        are skipped (not a hard failure -- friendlier for partial-dock
        scenarios). After issuing each enable_output, re-queries the WM and
        confirms the output actually reached the requested mode/position
        (not just that the IPC command was accepted) -- this is the exact
        "success:true but nothing actually changed" failure mode this tool
        was built to catch.
        """
        data = self._load_profile_file(label)
        result = LoadResult()

        connected = {m.unique_id: m for m in self.wm.get_outputs()}

        for entry in data.get("outputs", []):
            uid = entry["unique_id"]
            live = connected.get(uid)
            if live is None:
                result.skipped_not_connected.append(uid)
                continue

            if not entry.get("active", True):
                try:
                    self.wm.disable_output(live.name)
                    result.applied.append(uid)
                except WMCommandError as e:
                    result.failed.append({"unique_id": uid, "error": str(e)})
                continue

            mode_wh = entry["mode"].split("@")[0]  # "WxH@RRR.RRRHz" -> "WxH"
            try:
                self.wm.enable_output(
                    live.name,
                    mode=mode_wh,
                    position=entry.get("position", "0 0"),
                    scale=entry.get("scale", 1.0),
                    transform=entry.get("transform", "normal"),
                )
            except (WMCommandError, ValueError) as e:
                result.failed.append({"unique_id": uid, "error": str(e)})
                continue

            if self._verify_applied(uid, entry):
                result.applied.append(uid)
            else:
                result.failed.append({
                    "unique_id": uid,
                    "error": "Command accepted but output did not reach the requested "
                             "mode/position (verified after re-query).",
                })

        self.current_path.write_text(label)
        return result

    def _verify_applied(self, unique_id: str, entry: dict) -> bool:
        want_wh = entry["mode"].split("@")[0]
        want_pos = entry.get("position", "0 0")
        for _ in range(_APPLY_VERIFY_RETRIES):
            time.sleep(_APPLY_VERIFY_DELAY_SECONDS)
            live = next((m for m in self.wm.get_outputs() if m.unique_id == unique_id), None)
            if live is None:
                continue
            got_wh = f"{int(live.width)}x{int(live.height)}"
            got_pos = f"{live.pos_x} {live.pos_y}"
            if got_wh == want_wh and got_pos == want_pos:
                return True
        return False

    def rename_profile(self, old: str, new: str) -> None:
        old = validate_label(old)
        new = validate_label(new)
        with self._locked():
            self._check_not_locked(old)
            old_path = self._profile_path(old)
            if not old_path.exists():
                raise ProfileNotFoundError(f"No such profile: {old!r}")
            new_path = self._profile_path(new)
            if new_path.exists():
                raise InvalidLabelError(f"A profile named {new!r} already exists.")
            data = self._read_profile_raw(old_path) or {}
            data["label"] = new
            self._write_atomic(new_path, data)
            old_path.unlink()
            if self.get_current_label() == old:
                self.current_path.write_text(new)
            logger.info("Renamed profile %r -> %r", old, new)

    def remove_profile(self, label: str) -> None:
        with self._locked():
            self._check_not_locked(label)
            path = self._profile_path(label)
            if not path.exists():
                raise ProfileNotFoundError(f"No such profile: {label!r}")
            path.unlink()
            if self.get_current_label() == label:
                with contextlib.suppress(OSError):
                    self.current_path.unlink()
            logger.info("Removed profile %r", label)

    def lock_profile(self, label: str) -> None:
        with self._locked():
            data = self._load_profile_file(label)
            data["locked"] = True
            self._write_atomic(self._profile_path(label), data)

    def unlock_profile(self, label: str) -> None:
        with self._locked():
            data = self._load_profile_file(label)
            data["locked"] = False
            self._write_atomic(self._profile_path(label), data)

    def backup_profile(self, label: str) -> str:
        with self._locked():
            src = self._profile_path(label)
            if not src.exists():
                raise ProfileNotFoundError(f"No such profile: {label!r}")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_id = f"{label}__{timestamp}"
            dest = self.backups_dir / f"{backup_id}.json"
            shutil.copy2(src, dest)  # copy2 preserves the locked flag as data + mtime
            logger.info("Backed up profile %r -> %s", label, backup_id)
            return backup_id

    def list_backups(self, label: Optional[str] = None) -> List[str]:
        pattern = f"{label}__*.json" if label else "*.json"
        return sorted(p.stem for p in self.backups_dir.glob(pattern))

    def restore_backup(self, backup_id: str) -> str:
        """Restores a backup over its original label. Returns the label restored to.

        Refuses if the live profile currently exists and is locked (restoring
        over a locked profile would be a silent, unwanted mutation).
        """
        with self._locked():
            src = self.backups_dir / f"{backup_id}.json"
            if not src.exists():
                raise ProfileNotFoundError(f"No such backup: {backup_id!r}")
            data = self._read_profile_raw(src)
            if data is None:
                raise ProfileNotFoundError(f"Backup {backup_id!r} is corrupted.")
            label = data.get("label") or backup_id.rsplit("__", 1)[0]
            self._check_not_locked(label)
            self._write_atomic(self._profile_path(label), data)
            logger.info("Restored backup %r -> profile %r", backup_id, label)
            return label
