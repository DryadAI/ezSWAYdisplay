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
    ProfileWriteError,
    WMCommandError,
)
from .wm_adapter import Monitor, WMAdapter

logger = logging.getLogger(__name__)

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
_BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,199}$")
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


def verify_output_state(wm: WMAdapter, unique_id: str, *, want_wh: Optional[str] = None,
                         want_pos: Optional[str] = None, want_disabled: bool = False,
                         retries: int = _APPLY_VERIFY_RETRIES,
                         delay: float = _APPLY_VERIFY_DELAY_SECONDS) -> bool:
    """Polls wm.get_outputs() up to `retries` times, confirming a monitor
    actually reached the requested state -- a sway IPC "success: true" reply
    does not guarantee the change actually took effect (the class of bug
    this whole tool exists to catch). Shared between ProfileManager
    (save/load) and the GUI's drag-and-drop ArrangeCanvas.Apply, which
    previously reimplemented "call enable_output" without this check at all.

    A WM-unreachable blip during the poll itself is treated as "try again
    next retry", not a hard failure -- a transient IPC drop while polling
    (e.g. sway briefly restarting because of the very change being
    verified) shouldn't be indistinguishable from the change never applying.
    """
    for _ in range(retries):
        time.sleep(delay)
        try:
            outputs = wm.get_outputs()
        except WMCommandError:
            continue
        live = next((m for m in outputs if m.unique_id == unique_id), None)
        if want_disabled:
            if live is None or not live.active:
                return True
            continue
        if live is None:
            continue
        if want_wh is not None and f"{int(live.width)}x{int(live.height)}" != want_wh:
            continue
        if want_pos is not None and f"{live.pos_x} {live.pos_y}" != want_pos:
            continue
        return True
    return False


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


def validate_backup_id(backup_id: str) -> str:
    """Same character-safety rules as validate_label, but a longer max
    length (200 vs 64) -- a backup_id is "<label>__<timestamp>", which can
    legitimately exceed the 64-char label cap even for an otherwise-valid
    label. Reusing validate_label() for backup_id meant a profile with a
    41-64 char label (valid on its own) produced a backup that
    restore_backup() could never load back -- it would reject the backup_id
    for being "too long" before even checking whether the file existed.
    """
    if not backup_id or not backup_id.strip():
        raise InvalidLabelError("Backup id cannot be empty.")
    if backup_id in (".", ".."):
        raise InvalidLabelError(f"Backup id {backup_id!r} is not allowed.")
    if "/" in backup_id or "\\" in backup_id or ".." in backup_id:
        raise InvalidLabelError(f"Backup id {backup_id!r} cannot contain path separators.")
    if not _BACKUP_ID_RE.match(backup_id):
        raise InvalidLabelError(f"Backup id {backup_id!r} contains invalid characters.")
    return backup_id


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
        except OSError as e:
            # Every write in this class goes through this helper (or
            # _write_atomic_text below) -- previously neither wrapped its
            # OSError, so a disk-full/permission-denied condition during
            # any profile write (save/rename/lock/backup/restore, plus
            # .current) raised a bare OSError past every `except
            # EzSwayError` handler in the GUI/TUI/CLI, crashing instead of
            # showing the clean error message this error hierarchy exists
            # to guarantee.
            raise ProfileWriteError(f"Failed to write {path}: {e}") from e
        except (TypeError, ValueError) as e:
            # Matches config_store.py's ConfigStore.save(), which was
            # patched for the identical gap: only OSError was caught here,
            # so a non-JSON-serializable value ever ending up in a profile
            # dict would raise a bare TypeError past every `except
            # EzSwayError` handler.
            raise ProfileWriteError(f"Failed to serialize data for {path}: {e}") from e
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
            try:
                path.rename(corrupt_path)
            except OSError as rename_err:
                # Don't claim success in the log if the rename itself
                # failed (e.g. permission denied on the parent dir) --
                # previously this was unconditionally logged as "moved
                # aside to X" even when X was never actually created.
                logger.warning(
                    "Profile %s was corrupted and could not be moved aside: %s",
                    path.name, rename_err,
                )
            else:
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
        # load_profile was the one label-consuming method that never called
        # validate_label() -- every other mutating method (save/rename/
        # remove/lock/unlock/backup/restore) was patched for exactly this
        # path-traversal class of bug in an earlier pass, but this one
        # builds a filesystem path from the raw label just the same
        # (`ezswaydisplay load '../../../../etc/passwd'` would reach
        # open()+json.load() on an arbitrary file).
        label = validate_label(label)
        result = LoadResult()

        with self._locked():
            data = self._load_profile_file(label)
            connected = {m.unique_id: m for m in self.wm.get_outputs()}

            for entry in data.get("outputs", []):
                try:
                    uid = entry["unique_id"]
                except (KeyError, TypeError) as e:
                    # A hand-edited or future/older-schema profile could be
                    # missing required keys -- previously this raised a raw
                    # KeyError past every `except EzSwayError` handler in the
                    # GUI/TUI/CLI. Record it as a failure instead of crashing.
                    result.failed.append({"unique_id": "?", "error": f"Malformed profile entry: {e}"})
                    continue

                live = connected.get(uid)
                if live is None:
                    result.skipped_not_connected.append(uid)
                    continue

                if not entry.get("active", True):
                    try:
                        self.wm.disable_output(live.name)
                    except WMCommandError as e:
                        result.failed.append({"unique_id": uid, "error": str(e)})
                        continue
                    # Verified like the enable path below -- previously this
                    # branch marked `applied` unconditionally the instant
                    # disable_output() didn't raise, with no check that the
                    # output was actually off afterward. A sway refusal to
                    # go below its minimum-active-output floor (or any other
                    # silent no-op) would report success while the monitor
                    # stayed lit, the exact opposite of what was saved.
                    if self._verify_disabled(uid):
                        result.applied.append(uid)
                    else:
                        result.failed.append({
                            "unique_id": uid,
                            "error": "Command accepted but output was still active "
                                     "(verified after re-query).",
                        })
                    continue

                try:
                    mode_wh = entry["mode"].split("@")[0]  # "WxH@RRR.RRRHz" -> "WxH"
                except (KeyError, TypeError, AttributeError) as e:
                    # AttributeError added alongside KeyError/TypeError: a
                    # hand-edited or older/future-schema profile with
                    # "mode": 1920 (a number, not a string) raised
                    # AttributeError on .split() -- this block's whole point
                    # is to convert exactly this class of malformed-entry
                    # crash into a per-output failure instead of letting it
                    # propagate uncaught.
                    result.failed.append({"unique_id": uid, "error": f"Malformed profile entry: {e}"})
                    continue

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

            # Only claim this profile as "active" if it fully applied --
            # otherwise .current would point at a label whose real state on
            # screen doesn't match what was saved. Written atomically like
            # every other file this class touches (a crash mid-write must
            # never leave .current truncated/corrupted).
            if result.ok:
                self._write_atomic_text(self.current_path, label)

        return result

    def _write_atomic_text(self, path: Path, text: str):
        tmp = path.with_name(f".{path.name}.tmp")
        try:
            tmp.write_text(text)
            tmp.replace(path)
        except OSError as e:
            raise ProfileWriteError(f"Failed to write {path}: {e}") from e
        finally:
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()

    def _verify_applied(self, unique_id: str, entry: dict) -> bool:
        return verify_output_state(
            self.wm, unique_id,
            want_wh=entry["mode"].split("@")[0],
            want_pos=entry.get("position", "0 0"),
        )

    def _verify_disabled(self, unique_id: str) -> bool:
        return verify_output_state(self.wm, unique_id, want_disabled=True)

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
            try:
                old_path.unlink()
            except OSError as e:
                # If the old file survives (read-only mount, another process
                # holding it, etc.), the new copy at new_path already exists
                # -- both files present is a real inconsistent state worth
                # surfacing clearly rather than crashing with a bare OSError
                # past every `except EzSwayError` handler in the app.
                raise ProfileWriteError(
                    f"Renamed to {new!r} but could not remove the old file {old_path}: {e}"
                ) from e
            if self.get_current_label() == old:
                # Atomic like every other write in this class (load_profile
                # uses _write_atomic_text for the identical file) -- a raw
                # write_text() here could leave .current truncated/corrupted
                # on a crash mid-write during exactly the rename that's
                # supposed to keep it pointing at the right label.
                self._write_atomic_text(self.current_path, new)
            logger.info("Renamed profile %r -> %r", old, new)

    def remove_profile(self, label: str) -> None:
        label = validate_label(label)
        with self._locked():
            self._check_not_locked(label)
            path = self._profile_path(label)
            if not path.exists():
                raise ProfileNotFoundError(f"No such profile: {label!r}")
            try:
                path.unlink()
            except OSError as e:
                # Inconsistent with the current_path cleanup a few lines
                # below, which correctly suppresses OSError -- deleting the
                # *primary* profile file failing (read-only mount, immutable
                # attribute) is a real failure worth surfacing, not silently
                # swallowing.
                raise ProfileWriteError(f"Failed to remove {path}: {e}") from e
            if self.get_current_label() == label:
                with contextlib.suppress(OSError):
                    self.current_path.unlink()
            logger.info("Removed profile %r", label)

    def lock_profile(self, label: str) -> None:
        label = validate_label(label)
        with self._locked():
            data = self._load_profile_file(label)
            data["locked"] = True
            self._write_atomic(self._profile_path(label), data)

    def unlock_profile(self, label: str) -> None:
        label = validate_label(label)
        with self._locked():
            data = self._load_profile_file(label)
            data["locked"] = False
            self._write_atomic(self._profile_path(label), data)

    def backup_profile(self, label: str) -> str:
        label = validate_label(label)
        with self._locked():
            src = self._profile_path(label)
            if not src.exists():
                raise ProfileNotFoundError(f"No such profile: {label!r}")
            # Microsecond precision (not just seconds) plus an explicit
            # collision-avoidance loop as defense-in-depth: two backups of
            # the same profile within the same second used to compute an
            # identical backup_id and silently clobber each other via
            # copy2() -- exactly the snapshot this feature exists to
            # preserve, lost with no warning.
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup_id = f"{label}__{timestamp}"
            dest = self.backups_dir / f"{backup_id}.json"
            suffix = 1
            while dest.exists():
                backup_id = f"{label}__{timestamp}_{suffix}"
                dest = self.backups_dir / f"{backup_id}.json"
                suffix += 1
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
        # backup_id becomes a filename below (backups_dir/<backup_id>.json) --
        # same path-traversal exposure as a profile label, so it gets the
        # same character-safety validation, but via validate_backup_id (not
        # validate_label) since "<label>__<timestamp>" can legitimately
        # exceed the 64-char label cap.
        backup_id = validate_backup_id(backup_id)
        with self._locked():
            src = self.backups_dir / f"{backup_id}.json"
            if not src.exists():
                raise ProfileNotFoundError(f"No such backup: {backup_id!r}")
            data = self._read_profile_raw(src)
            if data is None:
                raise ProfileNotFoundError(f"Backup {backup_id!r} is corrupted.")
            label = validate_label(data.get("label") or backup_id.rsplit("__", 1)[0])
            self._check_not_locked(label)
            self._write_atomic(self._profile_path(label), data)
            logger.info("Restored backup %r -> profile %r", backup_id, label)
            return label
