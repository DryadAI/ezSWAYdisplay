import os
import json
import subprocess
import shutil
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
import i3ipc
import time
import sys

class Monitor:
    """Data class representing a connected monitor."""
    def __init__(self, name: str, make: str, model: str, serial: str, 
                 width: int, height: int, refresh_rate: float, 
                 scale: float = 1.0, active: bool = False,
                 pos_x: int = 0, pos_y: int = 0):
        self.name = name
        self.make = make
        self.model = model
        self.serial = serial
        self.width = width
        self.height = height
        self.refresh_rate = refresh_rate
        self.scale = scale
        self.active = active
        self.pos_x = pos_x
        self.pos_y = pos_y

    @property
    def unique_id(self) -> str:
        """Generates a unique ID for the monitor based on EDID data."""
        return f"{self.make}-{self.model}-{self.serial}"

    def __repr__(self):
        return f"<Monitor {self.name} ({self.unique_id}) Active={self.active}>"


class WMAdapter(ABC):
    """Abstract base class for Window Manager interactions."""

    @abstractmethod
    def get_outputs(self) -> List[Monitor]:
        """Returns a list of connected monitors."""
        pass

    @abstractmethod
    def enable_output(self, monitor_name: str, mode: str, position: str, scale: float = 1.0):
        """Enables a specific output with given configuration."""
        pass

    @abstractmethod
    def disable_output(self, monitor_name: str):
        """Disables a specific output."""
        pass

    @abstractmethod
    def reload_config(self):
        """Reloads the WM configuration."""
        pass


class SwayAdapter(WMAdapter):
    """Sway implementation of WMAdapter."""
    
    def __init__(self):
        try:
            self.ipc = i3ipc.Connection()
        except Exception as e:
            print(f"Failed to connect to Sway IPC: {e}", file=sys.stderr)
            self.ipc = None

    def get_outputs(self) -> List[Monitor]:
        if not self.ipc:
            # Fallback to swaymsg if IPC fails (unlikely if Sway is running)
            return self._get_outputs_fallback()
            
        outputs = self.ipc.get_outputs()
        monitors = []
        for out in outputs:
            # i3ipc output object attributes might vary slightly, 
            # ensuring safe access.
            make = getattr(out, 'make', 'Unknown')
            model = getattr(out, 'model', 'Unknown')
            serial = getattr(out, 'serial', 'Unknown')
            
            # If IPC doesn't provide EDID info directly in early versions, 
            # we might need to parse it or fallback. 
            # But standardized i3ipc should have it.
            
            rect = out.rect
            current_mode = out.current_mode
            
            width = rect.width
            height = rect.height
            refresh = 60.0 # Default
            
            if current_mode:
                 # i3ipc returns mode object
                 width = current_mode.width
                 height = current_mode.height
                 refresh = current_mode.refresh / 1000.0
            
            monitors.append(Monitor(
                name=out.name,
                make=make,
                model=model,
                serial=serial,
                width=width,
                height=height,
                refresh_rate=refresh,
                scale=out.scale if out.scale else 1.0,
                active=out.active,
                pos_x=rect.x,
                pos_y=rect.y
            ))
        return monitors

    def _get_outputs_fallback(self) -> List[Monitor]:
        """Fallback using swaymsg CLI."""
        try:
            result = subprocess.run(
                ["swaymsg", "-t", "get_outputs"],
                capture_output=True, text=True, check=True
            )
            data = json.loads(result.stdout)
            monitors = []
            for out in data:
                monitors.append(Monitor(
                    name=out.get("name"),
                    make=out.get("make", "Unknown"),
                    model=out.get("model", "Unknown"),
                    serial=out.get("serial", "Unknown"),
                    width=out.get("current_mode", {}).get("width", 0),
                    height=out.get("current_mode", {}).get("height", 0),
                    refresh_rate=out.get("current_mode", {}).get("refresh", 60000) / 1000.0,
                    scale=out.get("scale", 1.0),
                    active=out.get("active", False),
                    pos_x=out.get("rect", {}).get("x", 0),
                    pos_y=out.get("rect", {}).get("y", 0)
                ))
            return monitors
        except Exception as e:
            print(f"Fallback swaymsg failed: {e}", file=sys.stderr)
            return []

    def enable_output(self, monitor_name: str, mode: str, position: str, scale: float = 1.0):
        cmd = f"output {monitor_name} enable mode {mode} pos {position} scale {scale}"
        self._run_command(cmd)

    def disable_output(self, monitor_name: str):
        cmd = f"output {monitor_name} disable"
        self._run_command(cmd)

    def reload_config(self):
        self._run_command("reload")

    def _run_command(self, command: str):
        if self.ipc:
            self.ipc.command(command)
        else:
            subprocess.run(["swaymsg", command], check=False)


class HyprlandAdapter(WMAdapter):
    """Hyprland implementation of WMAdapter (Stub/Basic)."""
    
    def get_outputs(self) -> List[Monitor]:
        # TODO: Implement hyprctl monitors -j parsing
        return []

    def enable_output(self, monitor_name: str, mode: str, position: str, scale: float = 1.0):
        # hyprctl keyword monitor ...
        pass

    def disable_output(self, monitor_name: str):
        # hyprctl keyword monitor ... disabled
        pass

    def reload_config(self):
        subprocess.run(["hyprctl", "reload"], check=False)


class WMFactory:
    @staticmethod
    def create_adapter() -> WMAdapter:
        xdg_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        swaysock = os.environ.get("SWAYSOCK")
        hypr_sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        
        if swaysock or "sway" in xdg_desktop:
            return SwayAdapter()
        elif hypr_sig or "hyprland" in xdg_desktop:
            return HyprlandAdapter()
        else:
            # Default to Sway if unknown, or raise error
            # For now, let's assume Sway as per ezSWAYdisplay
            return SwayAdapter()
