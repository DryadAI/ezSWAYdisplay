"""Interactive terminal UI for ezSWAYdisplay's profile management.

Every action is wrapped so an error (locked profile, WM unreachable, bad
label, etc.) shows a clean inline message and returns to the menu -- never an
unhandled traceback dumped into what's supposed to be a clean terminal
session.
"""
import questionary

from ..core.errors import EzSwayError
from ..core.profile_manager import ProfileManager
from ..core.setup_wizard import SetupWizard
from ..core.wm_adapter import WMFactory

MAIN_MENU_CHOICES = [
    "Load a profile",
    "Save current layout as new profile",
    "Setup Wizard (capture current layout)",
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
    answer = questionary.select(prompt, choices=choices + ["(cancel)"]).ask()
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


def run_tui():
    wm = WMFactory.create_adapter()
    pm = ProfileManager(wm)
    wizard = SetupWizard(wm, pm)

    if wizard.is_first_run():
        print("No profiles saved yet -- let's capture your current layout.")
        if questionary.confirm("Run Setup Wizard now?", default=True).ask():
            try:
                label = wizard.run()
                _ok(f"Saved current layout as {label!r}.")
            except EzSwayError as e:
                _error(str(e))

    while True:
        choice = questionary.select("ezSWAYdisplay", choices=MAIN_MENU_CHOICES).ask()
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
                label = questionary.text("Label for this layout:").ask()
                if label:
                    pm.save_profile(label, wm.get_outputs())
                    _ok(f"Saved as {label!r}.")

            elif choice == "Setup Wizard (capture current layout)":
                label = questionary.text("Label (leave blank for auto):").ask()
                result_label = wizard.run(label=label or None)
                _ok(f"Saved current layout as {result_label!r}.")

            elif choice == "Rename a profile":
                old = _pick_label(pm, "Rename which profile?")
                if old:
                    new = questionary.text("New label:").ask()
                    if new:
                        pm.rename_profile(old, new)
                        _ok(f"Renamed {old!r} -> {new!r}.")

            elif choice == "Remove a profile":
                label = _pick_label(pm, "Remove which profile?")
                if label and questionary.confirm(f"Really remove {label!r}?", default=False).ask():
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
                    backup_id = questionary.select("Which backup?", choices=backups + ["(cancel)"]).ask()
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
