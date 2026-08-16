from typing import List
from .wm_adapter import WMFactory, WMAdapter, Monitor
from .config_store import ConfigStore
from .errors import ConfigStoreError, MonitorNotFoundError, WMCommandError
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MonitorManager:
    """Orchestrates monitor detection, policy enforcement, and configuration."""

    def __init__(self):
        self.wm: WMAdapter = WMFactory.create_adapter()
        self.config_store = ConfigStore()
        self.monitors: List[Monitor] = []

    def refresh_monitors(self) -> List[Monitor]:
        """Queries the WM for current monitor state.

        Raises WMCommandError if the WM can't be reached at all -- this is
        deliberately NOT swallowed into an empty list, so callers can tell
        "genuinely no monitors" apart from "can't talk to the WM".
        """
        self.monitors = self.wm.get_outputs()
        return self.monitors

    def enforce_policy(self):
        """
        Enforces the 'Default Deny' policy:
        1. Detect monitors.
        2. If a monitor is unknown, it should be disabled.
        3. If a monitor is known, apply its saved state (or keep active).
        4. FAIL-SAFE: Ensure at least one monitor remains active.

        Known caveat (tracked, not fixed here): a known monitor whose saved
        config says active=True but is currently reported inactive (e.g.
        disabled externally, or dropped by a flaky dock) is not currently
        reconciled back to active. See plan notes.
        """
        monitors = self.refresh_monitors()
        known_active_count = 0
        unknown_monitors = []

        # 1. Classification
        for m in monitors:
            if self.config_store.is_known(m.unique_id):
                if m.active:
                     known_active_count += 1
            else:
                unknown_monitors.append(m)

        # 2. Logic
        # If we have NO known active monitors, we MUST NOT disable everything.
        # If all detected monitors are unknown (fresh install), we must pick one to be active.

        if known_active_count == 0:
            if not unknown_monitors:
                logger.warning("No monitors detected at all!")
                return

            logger.info("No known active monitors. Engaging FAIL-SAFE.")

            active_unknowns = [m for m in unknown_monitors if m.active]
            if active_unknowns:
                # Keep the first active one as the "Safe" one; don't disable it below.
                safe_monitor = active_unknowns[0]
                logger.info(f"Fail-safe: Keeping {safe_monitor.name} active.")
                unknown_monitors.remove(safe_monitor)
            else:
                # No active monitors at all (headless start?). Enable first one.
                m = unknown_monitors[0]
                logger.info(f"Fail-safe: Activating {m.name}.")
                try:
                    # Use the monitor's own detected mode, not the literal
                    # string "preferred" -- sway has no such mode keyword.
                    self.wm.enable_output(
                        m.name,
                        mode=f"{m.width}x{m.height}",
                        position="0 0",
                    )
                    self.config_store.set_monitor_config(m.unique_id, {
                        "active": True,
                        "mode": f"{m.width}x{m.height}",
                    })
                except (WMCommandError, ConfigStoreError) as e:
                    # ConfigStoreError (e.g. disk full/permission denied on
                    # monitors.json) used to be uncaught here, since this
                    # branch only handled WMCommandError -- it would
                    # propagate out of enforce_policy() entirely, skipping
                    # step 3 below and leaving other unknown monitors
                    # active, violating the policy's own "default deny".
                    logger.error(f"Fail-safe activation of {m.name} failed: {e}")
                else:
                    unknown_monitors.remove(m)

        # 3. Disable the rest of unknown monitors
        for m in unknown_monitors:
            if m.active:
                logger.info(f"Disabling unknown monitor: {m.name} ({m.unique_id})")
                try:
                    self.wm.disable_output(m.name)
                except WMCommandError as e:
                    logger.error(f"Failed to disable unknown monitor {m.name}: {e}")
                # We do NOT save this state, so it remains "unknown" until user explicitly configures it.

    def activate_monitor(self, unique_id: str):
        """
        Called by GUI to authorize a monitor.
        1. Find monitor by ID.
        2. Set config to active.
        3. Save to store.
        4. Apply.

        Raises WMCommandError / ConfigStoreError on failure -- callers must
        catch and surface these, not assume success.
        """
        target = next((m for m in self.monitors if m.unique_id == unique_id), None)
        if not target:
            self.refresh_monitors()
            target = next((m for m in self.monitors if m.unique_id == unique_id), None)

        if not target:
            # Previously logged and silently returned, contradicting this
            # method's own docstring ("Raises ... on failure -- callers must
            # catch and surface these, not assume success"). A caller
            # wrapping this in try/except EzSwayError (as MainWindow does)
            # saw no exception and no error dialog -- the click just did
            # nothing, with no feedback at all.
            raise MonitorNotFoundError(f"Cannot activate {unique_id!r}: monitor not connected.")

        config = {
            "active": True,
            "mode": f"{target.width}x{target.height}",
            "position": f"{target.pos_x} {target.pos_y}",
            "scale": target.scale
        }

        self.wm.enable_output(
            target.name,
            mode=f"{target.width}x{target.height}",
            position=f"{target.pos_x} {target.pos_y}",
            scale=target.scale,
        )
        self.config_store.set_monitor_config(unique_id, config)
        logger.info(f"Activated monitor {target.name}")

    def deactivate_monitor(self, unique_id: str):
        """Called by GUI to disable a monitor.

        Re-fetches if the monitor isn't in the last-known list (matches
        activate_monitor's behavior -- previously this silently no-op'd if
        the monitor had vanished from self.monitors since the last refresh).
        """
        target = next((m for m in self.monitors if m.unique_id == unique_id), None)
        if not target:
            self.refresh_monitors()
            target = next((m for m in self.monitors if m.unique_id == unique_id), None)

        if not target:
            raise MonitorNotFoundError(f"Cannot deactivate {unique_id!r}: monitor not connected.")

        self.wm.disable_output(target.name)
        self.config_store.set_monitor_config(unique_id, {"active": False})
        logger.info(f"Disabled monitor {target.name}")
