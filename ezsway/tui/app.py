"""Interactive terminal UI for ezSWAYdisplay's profile management.

Every action is wrapped so an error (locked profile, WM unreachable, bad
label, etc.) shows a clean inline message and returns to the menu -- never an
unhandled traceback dumped into what's supposed to be a clean terminal
session.
"""
import questionary

from ..core.errors import EzSwayError
from ..core.monitor_manager import MonitorManager
from ..core.profile_manager import ProfileManager
from ..core.setup_wizard import SetupWizard
from ..core.wm_adapter import WMFactory
from .arrange import run_arrange

MAIN_MENU_CHOICES = [
    "Load a profile",
    "Arrange displays (move with arrow keys)",
    "Save current layout as new profile",
    "Setup Wizard (capture current layout)",
    "Set up a new display (activate/deactivate)",
    "Rename a profile",
    "Remove a profile",
    "Lock a profile",
    "Unlock a profile",
    "Backup a profile",
    "Restore from backup",
    "List profiles",
    "Exit",
]


def _error(msg: str):
    print(f"\n✗ {msg}\n")


def _ok(msg: str):
    print(f"\n✓ {msg}\n")


def _ask(question):
    """Runs a questionary question and returns its answer, or None on
    cancel/EOF/interrupt.

    questionary's own convention is that a cancelled prompt (Ctrl+C, Esc)
    returns None from .ask() -- but a genuinely non-interactive or detached
    stdin (no controlling terminal) makes prompt_toolkit raise a raw
    EOFError instead of returning None, and that previously wasn't caught
    anywhere, crashing with a traceback instead of the same clean "user
    backed out" behavior every None-check in this file already handles.
    """
    try:
        return question.ask()
    except (EOFError, KeyboardInterrupt):
        return None


def _pick_label(pm: ProfileManager, prompt: str = "Which profile?"):
    profiles = pm.list_profiles()
    if not profiles:
        _error("No saved profiles yet. Try 'Setup Wizard' first.")
        return None
    choices = [
        f"{p['label']}" + (" [active]" if p["active"] else "") + (" [locked]" if p["locked"] else "")
        for p in profiles
    ]
    label_map = {c: p["label"] for c, p in zip(choices, profiles)}
    answer = _ask(questionary.select(prompt, choices=choices + ["(cancel)"]))
    if answer is None or answer == "(cancel)":
        return None
    return label_map[answer]


def _list_profiles(pm: ProfileManager):
    profiles = pm.list_profiles()
    if not profiles:
        print("No saved profiles yet.")
        return
    for p in profiles:
        flags = []
        if p["active"]:
            flags.append("active")
        if p["locked"]:
            flags.append("locked")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        print(f"  {p['label']}{flag_str} - {p['output_count']} output(s)")
    print()


def _manage_monitors(manager: MonitorManager):
    """Lets you activate a new/unknown monitor (the default-deny policy
    disables anything it hasn't seen before) or deactivate a known one --
    the TUI's counterpart to the GUI's per-monitor activate/deactivate
    buttons, which this app has had since the policy was built but never
    exposed here."""
    monitors = manager.refresh_monitors()
    if not monitors:
        _error("No monitors detected.")
        return

    def describe(m):
        known = manager.config_store.is_known(m.unique_id)
        status = []
        status.append("known" if known else "unknown")
        status.append("active" if m.active else "inactive")
        return f"{m.name} ({m.make} {m.model} {m.serial}) - {', '.join(status)}"

    choices = [describe(m) for m in monitors]
    monitor_map = dict(zip(choices, monitors))
    answer = _ask(questionary.select("Which display?", choices=choices + ["(cancel)"]))
    if answer is None or answer == "(cancel)":
        return

    m = monitor_map[answer]
    known = manager.config_store.is_known(m.unique_id)
    action = "Deactivate" if (known and m.active) else "Activate"
    if not _ask(questionary.confirm(f"{action} {m.name}?", default=True)):
        return

    if action == "Activate":
        manager.activate_monitor(m.unique_id)
        _ok(f"Activated {m.name}.")
    else:
        manager.deactivate_monitor(m.unique_id)
        _ok(f"Deactivated {m.name}.")


def run_tui():
    wm = WMFactory.create_adapter()
    pm = ProfileManager(wm)
    wizard = SetupWizard(wm, pm)
    manager = MonitorManager()

    if wizard.is_first_run():
        print("No profiles saved yet -- let's capture your current layout.")
        if _ask(questionary.confirm("Run Setup Wizard now?", default=True)):
            try:
                label = wizard.run()
                _ok(f"Saved current layout as {label!r}.")
            except EzSwayError as e:
                _error(str(e))

    while True:
        choice = _ask(questionary.select("ezSWAYdisplay", choices=MAIN_MENU_CHOICES))
        if choice is None or choice == "Exit":
            break

        try:
            if choice == "Load a profile":
                label = _pick_label(pm)
                if label:
                    result = pm.load_profile(label)
                    if result.ok:
                        _ok(f"Loaded {label!r} ({len(result.applied)} output(s) applied).")
                    else:
                        _error(
                            f"Loaded {label!r} with {len(result.failed)} failure(s): "
                            + "; ".join(f"{f['unique_id']}: {f['error']}" for f in result.failed)
                        )
                    if result.skipped_not_connected:
                        print(f"  (skipped, not connected: {', '.join(result.skipped_not_connected)})")

            elif choice == "Save current layout as new profile":
                label = _ask(questionary.text("Label for this layout:"))
                if label:
                    pm.save_profile(label, wm.get_outputs())
                    _ok(f"Saved as {label!r}.")

            elif choice == "Setup Wizard (capture current layout)":
                label = _ask(questionary.text("Label (leave blank for auto):"))
                result_label = wizard.run(label=label or None)
                _ok(f"Saved current layout as {result_label!r}.")

            elif choice == "Arrange displays (move with arrow keys)":
                run_arrange(wm, pm)

            elif choice == "Set up a new display (activate/deactivate)":
                _manage_monitors(manager)

            elif choice == "Rename a profile":
                old = _pick_label(pm, "Rename which profile?")
                if old:
                    new = _ask(questionary.text("New label:"))
                    if new:
                        pm.rename_profile(old, new)
                        _ok(f"Renamed {old!r} -> {new!r}.")

            elif choice == "Remove a profile":
                label = _pick_label(pm, "Remove which profile?")
                if label and _ask(questionary.confirm(f"Really remove {label!r}?", default=False)):
                    pm.remove_profile(label)
                    _ok(f"Removed {label!r}.")

            elif choice == "Lock a profile":
                label = _pick_label(pm, "Lock which profile?")
                if label:
                    pm.lock_profile(label)
                    _ok(f"Locked {label!r}.")

            elif choice == "Unlock a profile":
                label = _pick_label(pm, "Unlock which profile?")
                if label:
                    pm.unlock_profile(label)
                    _ok(f"Unlocked {label!r}.")

            elif choice == "Backup a profile":
                label = _pick_label(pm, "Back up which profile?")
                if label:
                    backup_id = pm.backup_profile(label)
                    _ok(f"Backed up as {backup_id}.")

            elif choice == "Restore from backup":
                backups = pm.list_backups()
                if not backups:
                    _error("No backups yet.")
                else:
                    backup_id = _ask(questionary.select("Which backup?", choices=backups + ["(cancel)"]))
                    if backup_id and backup_id != "(cancel)":
                        restored_label = pm.restore_backup(backup_id)
                        _ok(f"Restored -> profile {restored_label!r}.")

            elif choice == "List profiles":
                _list_profiles(pm)

        except EzSwayError as e:
            _error(str(e))
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    run_tui()
