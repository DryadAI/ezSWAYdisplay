#!/usr/bin/env python3
import argparse
import logging
import sys

from .core.errors import EzSwayError
from .core.monitor_manager import MonitorManager
from .core.profile_manager import ProfileManager
from .core.setup_wizard import SetupWizard
from .core.wm_adapter import WMFactory

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _build_profile_manager() -> ProfileManager:
    wm = WMFactory.create_adapter()
    return ProfileManager(wm)


def cmd_profiles(args):
    pm = _build_profile_manager()
    profiles = pm.list_profiles()
    if not profiles:
        print("No saved profiles yet. Run 'ezswaydisplay setup' to create one.")
        return
    for p in profiles:
        flags = []
        if p["active"]:
            flags.append("active")
        if p["locked"]:
            flags.append("locked")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        print(f"{p['label']}{flag_str} - {p['output_count']} output(s)")


def cmd_save(args):
    pm = _build_profile_manager()
    pm.save_profile(args.label, pm.wm.get_outputs())
    print(f"Saved profile {args.label!r}.")


def cmd_load(args):
    pm = _build_profile_manager()
    result = pm.load_profile(args.label)
    print(f"Applied: {', '.join(result.applied) or '(none)'}")
    if result.skipped_not_connected:
        print(f"Skipped (not connected): {', '.join(result.skipped_not_connected)}")
    if result.failed:
        print("FAILED to apply:")
        for f in result.failed:
            print(f"  {f['unique_id']}: {f['error']}")
        sys.exit(1)


def cmd_rename(args):
    pm = _build_profile_manager()
    pm.rename_profile(args.old, args.new)
    print(f"Renamed {args.old!r} -> {args.new!r}.")


def cmd_remove(args):
    pm = _build_profile_manager()
    pm.remove_profile(args.label)
    print(f"Removed profile {args.label!r}.")


def cmd_lock(args):
    pm = _build_profile_manager()
    pm.lock_profile(args.label)
    print(f"Locked profile {args.label!r}.")


def cmd_unlock(args):
    pm = _build_profile_manager()
    pm.unlock_profile(args.label)
    print(f"Unlocked profile {args.label!r}.")


def cmd_backup(args):
    pm = _build_profile_manager()
    backup_id = pm.backup_profile(args.label)
    print(f"Backed up {args.label!r} -> {backup_id}")


def cmd_restore(args):
    pm = _build_profile_manager()
    label = pm.restore_backup(args.backup_id)
    print(f"Restored backup {args.backup_id!r} -> profile {label!r}.")


def cmd_setup(args):
    pm = _build_profile_manager()
    wizard = SetupWizard(pm.wm, pm)
    label = wizard.run(label=args.label)
    print(f"Setup complete. Saved current layout as {label!r}.")


def cmd_gui(args):
    from PyQt6.QtWidgets import QApplication
    from .gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ezSWAYdisplay")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def cmd_tui(args):
    from .tui.app import run_tui
    run_tui()


def cmd_enforce(args):
    """Legacy default behavior: run the monitor-authorization policy once."""
    manager = MonitorManager()
    manager.enforce_policy()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ezswaydisplay",
        description="Display manager for Sway (and, eventually, Hyprland): "
                     "monitor authorization policy + named saved layouts.",
    )
    parser.add_argument("--gui", action="store_true", help="Launch the GUI (default if no subcommand given)")
    parser.add_argument("--tui", action="store_true", help="Launch the interactive terminal UI")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("profiles", help="List saved profiles").set_defaults(func=cmd_profiles)

    p = sub.add_parser("save", help="Save the current layout as a named profile")
    p.add_argument("label")
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("load", help="Apply a saved profile")
    p.add_argument("label")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("rename", help="Rename a profile")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("remove", help="Delete a profile")
    p.add_argument("label")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("lock", help="Lock a profile against changes")
    p.add_argument("label")
    p.set_defaults(func=cmd_lock)

    p = sub.add_parser("unlock", help="Unlock a profile")
    p.add_argument("label")
    p.set_defaults(func=cmd_unlock)

    p = sub.add_parser("backup", help="Back up a profile")
    p.add_argument("label")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="Restore a profile from a backup")
    p.add_argument("backup_id")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("setup", help="Setup Wizard: capture the current layout as a new profile")
    p.add_argument("label", nargs="?", default=None, help="Optional label (default: hardware fingerprint)")
    p.set_defaults(func=cmd_setup)

    sub.add_parser("enforce", help="Run the monitor-authorization policy once (headless/cron use)").set_defaults(func=cmd_enforce)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # All three paths (TUI/GUI/CLI subcommand) go through the same
    # try/except now -- previously cmd_tui/cmd_gui ran *before* this block,
    # so e.g. a Hyprland user (WMFactory.create_adapter() raising
    # WMNotSupportedError during MainWindow()/SetupWizard construction) got
    # a raw traceback instead of the clean "Error: ..." message every CLI
    # subcommand already had.
    try:
        if args.tui:
            cmd_tui(args)
        elif args.command is None or args.gui:
            cmd_gui(args)
        else:
            args.func(args)
    except EzSwayError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
