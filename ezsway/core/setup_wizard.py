"""First-run / on-demand setup: capture the current monitor arrangement and
save it as a named profile.

This supersedes the standalone legacy ezSWAYdisplay.py script's flow (query
outputs -> generate config -> back up existing -> write), which is kept
working as-is for anyone still invoking it directly, but had two gaps this
version corrects while porting the same shape:
  1. It keyed output blocks by raw connector name, not hardware identity, so
     it didn't survive port renumbering the way the rest of this codebase
     does.
  2. It only ever wrote one hardcoded file -- no naming/profiles.
"""
import logging
from typing import List, Optional

from .errors import ProfileNotFoundError, WMCommandError, WMNotReachableError
from .profile_manager import ProfileManager
from .wm_adapter import Monitor, WMAdapter

logger = logging.getLogger(__name__)


def default_fingerprint_label(monitors: List[Monitor]) -> str:
    """Same style of hardware fingerprint used elsewhere in this ecosystem
    (sorted Make_Model, joined) -- used as the default label when the user
    doesn't supply one.

    Falls back to fingerprinting ALL detected monitors (not just active
    ones) if none happen to be active -- e.g. right after enforce_policy()
    has default-denied every unknown monitor, where the monitor list is
    non-empty but every entry currently reports active=False. Without this,
    every such transitional state on any machine collapsed to the same
    literal "default" label, so two unrelated hardware configurations could
    silently overwrite the same profile.
    """
    active_parts = sorted(
        f"{m.make}_{m.model}".replace(" ", "_") for m in monitors if m.active
    )
    if active_parts:
        return "_".join(active_parts)
    all_parts = sorted(f"{m.make}_{m.model}".replace(" ", "_") for m in monitors)
    return "_".join(all_parts) or "default"


class SetupWizard:
    def __init__(self, wm_adapter: WMAdapter, profile_manager: ProfileManager):
        self.wm = wm_adapter
        self.profiles = profile_manager

    def get_current_outputs(self) -> List[Monitor]:
        """Raises WMNotReachableError with a clear message if the WM can't be
        reached at all -- previously this class of failure could surface as a
        raw FileNotFoundError/ConnectionError with no useful message."""
        try:
            return self.wm.get_outputs()
        except WMCommandError as e:
            raise WMNotReachableError(
                "Sway does not appear to be running (or its IPC socket is "
                f"unreachable): {e}"
            ) from e

    def run(self, label: Optional[str] = None) -> str:
        """Captures the current layout and saves it as a profile.

        If `label` is omitted, uses a hardware-fingerprint default. If a
        profile with that label already exists, it is backed up first (never
        silently overwritten with no recovery path -- unlike the legacy
        script's single hardcoded file, every setup run here is recoverable).

        Returns the label saved to.
        """
        monitors = self.get_current_outputs()
        if not monitors:
            raise WMNotReachableError("No monitors detected -- nothing to save.")

        resolved_label = label or default_fingerprint_label(monitors)

        try:
            self.profiles.backup_profile(resolved_label)
            logger.info("Existing profile %r backed up before overwrite.", resolved_label)
        except ProfileNotFoundError:
            pass  # nothing to back up -- this is a genuinely new profile

        self.profiles.save_profile(resolved_label, monitors)
        logger.info("Setup complete: saved current layout as %r.", resolved_label)
        return resolved_label

    def is_first_run(self) -> bool:
        """True if no profiles have ever been saved -- used to auto-offer the
        wizard on a fresh install."""
        return len(self.profiles.list_profiles()) == 0
