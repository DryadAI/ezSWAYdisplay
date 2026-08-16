import os
import json
import subprocess
from abc import ABC, abstractmethod
from typing import List
import i3ipc
import sys

from .errors import WMCommandError, WMNotSupportedError

VALID_TRANSFORMS = {
    "normal", "90", "180", "270",
    "flipped", "flipped-90", "flipped-180", "flipped-270",
}


class Monitor:
    """Data class representing a connected monitor."""
    def __init__(self, name: str, make: str, model: str, serial: str,
                 width: int, height: int, refresh_rate: float,
                 scale: float = 1.0, active: bool = False,
                 pos_x: int = 0, pos_y: int = 0, transform: str = "normal"):
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
        self.transform = transform

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
    def enable_output(self, monitor_name: str, mode: str, position: str,
                       scale: float = 1.0, transform: str = "normal"):
        """Enables a specific output with given configuration.

        Raises WMCommandError if the WM rejects the command.
        """
        pass

    @abstractmethod
    def disable_output(self, monitor_name: str):
        """Disables a specific output. Raises WMCommandError on rejection."""
        pass

    @abstractmethod
    def reload_config(self):
        """Reloads the WM configuration."""
        pass


def _validate_transform(transform: str):
    if transform not in VALID_TRANSFORMS:
        raise ValueError(
            f"Invalid transform {transform!r}; must be one of {sorted(VALID_TRANSFORMS)}"
        )


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

        try:
            outputs = self.ipc.get_outputs()
        except Exception as e:
            # The IPC connection can drop/error mid-session (socket closed,
            # sway restarted, etc.) well after a successful __init__ -- this
            # used to be completely unguarded, unlike every other IPC call
            # path in this class, producing a raw traceback instead of a
            # catchable WMCommandError.
            raise WMCommandError(f"Failed to query sway outputs via IPC: {e}") from e

        monitors = []
        for out in outputs:
            # i3ipc output object attributes might vary slightly,
            # ensuring safe access.
            make = getattr(out, 'make', 'Unknown')
            model = getattr(out, 'model', 'Unknown')
            serial = getattr(out, 'serial', 'Unknown')

            rect = out.rect
            current_mode = out.current_mode

            width = rect.width
            height = rect.height
            refresh = 60.0  # Default

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
                pos_y=rect.y,
                transform=getattr(out, 'transform', None) or "normal",
            ))
        return monitors

    def _get_outputs_fallback(self) -> List[Monitor]:
        """Fallback using swaymsg CLI. Raises WMCommandError if swaymsg itself
        cannot be reached at all (caller should treat this as WM-not-running,
        not as "zero monitors")."""
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
                    pos_y=out.get("rect", {}).get("y", 0),
                    transform=out.get("transform") or "normal",
                ))
            return monitors
        except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError) as e:
            raise WMCommandError(f"Cannot query sway outputs (is sway running?): {e}") from e

    def enable_output(self, monitor_name: str, mode: str, position: str,
                       scale: float = 1.0, transform: str = "normal"):
        _validate_transform(transform)
        cmd = (
            f"output {monitor_name} enable mode {mode} pos {position} "
            f"scale {scale} transform {transform}"
        )
        self._run_command(cmd)

    def disable_output(self, monitor_name: str):
        cmd = f"output {monitor_name} disable"
        self._run_command(cmd)

    def reload_config(self):
        self._run_command("reload")

    def _run_command(self, command: str):
        """Runs a sway command and raises WMCommandError if sway rejects it.

        sway's IPC command reply is a list of {"success": bool, "error": str}
        objects, one per semicolon-separated sub-command. Previously this
        fired-and-forgot -- a rejected command (bad mode, bad transform,
        monitor gone) failed completely silently.
        """
        if self.ipc:
            try:
                replies = self.ipc.command(command)
            except Exception as e:
                raise WMCommandError(f"sway IPC command failed: {command!r}: {e}") from e
            errors = [
                getattr(r, 'error', None) for r in replies
                if not getattr(r, 'success', True)
            ]
            if errors:
                raise WMCommandError(
                    f"sway rejected command {command!r}: {'; '.join(e for e in errors if e)}"
                )
        else:
            result = subprocess.run(["swaymsg", command], capture_output=True, text=True)
            if result.returncode != 0:
                raise WMCommandError(
                    f"swaymsg rejected command {command!r}: {result.stderr.strip()}"
                )


class HyprlandAdapter(WMAdapter):
    """Hyprland implementation of WMAdapter (Stub/Basic) -- NOT YET FUNCTIONAL.

    Intentionally raises WMNotSupportedError from WMFactory rather than being
    instantiated silently; see WMFactory.create_adapter(). Methods here also
    raise WMNotSupportedError (not bare NotImplementedError) in case this
    class is ever instantiated directly, bypassing the factory -- callers
    throughout this codebase catch `EzSwayError`, and a bare
    NotImplementedError wouldn't be caught by that, producing an unhandled
    traceback instead of the clean error message every other failure path
    here promises.
    """

    def get_outputs(self) -> List[Monitor]:
        # TODO: Implement hyprctl monitors -j parsing
        raise WMNotSupportedError("Hyprland support is not yet implemented")

    def enable_output(self, monitor_name: str, mode: str, position: str,
                       scale: float = 1.0, transform: str = "normal"):
        # TODO: hyprctl keyword monitor ...
        raise WMNotSupportedError("Hyprland support is not yet implemented")

    def disable_output(self, monitor_name: str):
        # TODO: hyprctl keyword monitor ... disabled
        raise WMNotSupportedError("Hyprland support is not yet implemented")

    def reload_config(self):
        result = subprocess.run(["hyprctl", "reload"], capture_output=True, text=True)
        if result.returncode != 0:
            raise WMCommandError(f"hyprctl reload failed: {result.stderr.strip()}")


class WMFactory:
    @staticmethod
    def create_adapter() -> WMAdapter:
        xdg_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        swaysock = os.environ.get("SWAYSOCK")
        hypr_sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")

        if swaysock or "sway" in xdg_desktop:
            return SwayAdapter()
        elif hypr_sig or "hyprland" in xdg_desktop:
            # Hyprland is detected but not yet implemented -- fail loudly
            # instead of handing back a stub that silently no-ops every call
            # (a Hyprland user previously got an empty monitor list and no
            # error at all, despite the README claiming WM-agnostic support).
            raise WMNotSupportedError(
                "Hyprland support is not yet implemented (architecture is in "
                "place in wm_adapter.HyprlandAdapter, but get_outputs/enable_output/"
                "disable_output are unfinished). Falling back to Sway would be wrong "
                "since you're not running Sway -- please use the legacy CLI or wait "
                "for Hyprland support to land."
            )
        else:
            # Default to Sway if unknown
            return SwayAdapter()
