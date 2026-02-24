from typing import List, Dict, Optional
from .wm_adapter import WMFactory, WMAdapter, Monitor
from .config_store import ConfigStore
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
        """Queries the WM for current monitor state."""
        self.monitors = self.wm.get_outputs()
        return self.monitors

    def enforce_policy(self):
        """
        Enforces the 'Default Deny' policy:
        1. Detect monitors.
        2. If a monitor is unknown, it should be disabled.
        3. If a monitor is known, apply its saved state (or keep active).
        4. FAIL-SAFE: Ensure at least one monitor remains active.
        """
        monitors = self.refresh_monitors()
        known_active_count = 0
        unknown_monitors = []
        
        # 1. Classification
        for m in monitors:
            if self.config_store.is_known(m.unique_id):
                config = self.config_store.get_monitor_config(m.unique_id)
                # If known and configured to be active (or just known), we count it as a potential active
                # For now, let's say if it's known, we respect its current state or saved state.
                # But to simple 'default deny', we really care about UNKNOWN ones.
                if m.active:  # Or check config['active']
                     known_active_count += 1
            else:
                unknown_monitors.append(m)

        # 2. Logic
        # If we have NO known active monitors, we MUST NOT disable everything.
        # Check if we have any known monitors at all? 
        # If all monitors are unknown (fresh install), we must pick one to be active.
        
        safe_to_disable = True
        if known_active_count == 0:
            if not unknown_monitors:
                logger.warning("No monitors detected at all!")
                return
            
            # Scenario: All detected monitors are unknown.
            # We must keep at least one active.
            logger.info("No known active monitors. Engaging FAIL-SAFE.")
            
            # If there's already an active unknown monitor, keep it active (don't disable it).
            # If all are disabled, enable one.
            
            active_unknowns = [m for m in unknown_monitors if m.active]
            if active_unknowns:
                # Keep the first one, disable others? Or just keep one?
                # Let's keep the first active one as the "Safe" one.
                safe_monitor = active_unknowns[0]
                logger.info(f"Fail-safe: Keeping {safe_monitor.name} active.")
                unknown_monitors.remove(safe_monitor) # Don't disable this one
            else:
                # No active monitors at all (headless start?). Enable first one.
                m = unknown_monitors[0]
                logger.info(f"Fail-safe: Activating {m.name}.")
                # key = m.make + " " + m.model... 
                self.config_store.set_monitor_config(m.unique_id, {
                    "active": True,
                    "mode": f"{m.width}x{m.height}", # default
                    # ... other defaults
                })
                self.wm.enable_output(m.name, mode="preferred", position="0 0") 
                unknown_monitors.remove(m)

        # 3. Disable the rest of unknown monitors
        for m in unknown_monitors:
            if m.active:
                logger.info(f"Disabling unknown monitor: {m.name} ({m.unique_id})")
                self.wm.disable_output(m.name)
                # We do NOT save this state, so it remains "unknown" until user explicitly configures it.
                
    def activate_monitor(self, unique_id: str):
        """
        Called by GUI to authorize a monitor.
        1. Find monitor by ID.
        2. Set config to active.
        3. Save to store.
        4. Apply.
        """
        # Find the monitor object
        target = next((m for m in self.monitors if m.unique_id == unique_id), None)
        if not target:
            # Maybe refresh?
            self.refresh_monitors()
            target = next((m for m in self.monitors if m.unique_id == unique_id), None)
            
        if not target:
            logger.error(f"Cannot activate {unique_id}: Monitor not found connected.")
            return

        # Create default config if not exists
        config = {
            "active": True,
            "mode": f"{target.width}x{target.height}", # Uses current detected mode as default
            "position": f"{target.pos_x} {target.pos_y}",
            "scale": target.scale
        }
        
        self.config_store.set_monitor_config(unique_id, config)
        self.wm.enable_output(target.name, mode="preferred", position=f"{target.pos_x} {target.pos_y}", scale=target.scale)
        logger.info(f"Activated monitor {target.name}")

    def deactivate_monitor(self, unique_id: str):
        """Called by GUI to disable a monitor."""
        target = next((m for m in self.monitors if m.unique_id == unique_id), None)
        if target:
             self.config_store.set_monitor_config(unique_id, {"active": False})
             self.wm.disable_output(target.name)
             logger.info(f"Disabled monitor {target.name}")

