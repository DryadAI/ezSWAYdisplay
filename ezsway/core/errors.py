"""Shared exception types for ezsway.core.

Callers (GUI/TUI/CLI) are expected to catch these specifically and show
a real message to the user rather than letting a raw traceback surface.
"""


class EzSwayError(Exception):
    """Base class for all ezsway.core errors."""


class ConfigStoreError(EzSwayError):
    """Raised when persisting the monitor-authorization store fails."""


class InvalidLabelError(EzSwayError):
    """Raised when a profile label is empty or filesystem-unsafe."""


class ProfileLockedError(EzSwayError):
    """Raised when attempting to mutate a locked profile."""


class ProfileNotFoundError(EzSwayError):
    """Raised when a referenced profile label does not exist."""


class WMCommandError(EzSwayError):
    """Raised when the window manager rejects a command (IPC reply success=False)."""


class ConcurrentAccessError(EzSwayError):
    """Raised when the profiles lock is already held by another operation."""


class WMNotReachableError(EzSwayError):
    """Raised when no window-manager IPC can be reached at all (WM not running)."""


class WMNotSupportedError(EzSwayError):
    """Raised when the detected window manager has no working adapter yet
    (e.g. Hyprland: architecture in place, not yet implemented)."""


class MonitorNotFoundError(EzSwayError):
    """Raised when a referenced monitor unique_id isn't among currently
    connected outputs."""
