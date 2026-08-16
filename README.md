# ezSWAYdisplay

**A TUI + GUI display manager for Sway** (Hyprland support planned, not yet functional).

Two complementary features:
- **Monitor authorization policy** ("default deny"): new monitors are disabled until you explicitly activate them, so plugging something in never sends windows jumping to a random screen. Always keeps at least one monitor active (fail-safe).
- **Saved layouts ("profiles")**: name and save a full multi-monitor arrangement, then load it back later by name. Matched by hardware identity (make/model/serial), not port name, so it survives docks/hubs renumbering connectors between boots.

## Installation

```bash
git clone https://github.com/NModlin/ezSWAYdisplay.git
cd ezSWAYdisplay
./install.sh
```

`install.sh` creates an isolated virtualenv, installs dependencies into it, adds an app-menu launcher (`.desktop` entry), and offers (skippable, never forced) to add a sway autostart line and a keybinding for the TUI. It's safe to re-run any time — it only touches what's missing, and always backs up your sway config before editing it.

Flags: `./install.sh --no-autostart` / `./install.sh --no-keybind` to skip those prompts entirely.

## Usage

```bash
./run_gui.sh          # GUI (also reachable from your app menu / launcher)
./run_tui.sh           # Interactive terminal UI
```

Or via the CLI directly, for scripting:
```bash
python ezsway/main.py profiles              # list saved profiles
python ezsway/main.py setup [label]         # Setup Wizard: capture current layout
python ezsway/main.py save <label>
python ezsway/main.py load <label>
python ezsway/main.py rename <old> <new>
python ezsway/main.py remove <label>
python ezsway/main.py lock <label>
python ezsway/main.py unlock <label>
python ezsway/main.py backup <label>
python ezsway/main.py restore <backup_id>
```

### Setup Wizard
On first run (no profiles saved yet), both the GUI and TUI offer to run the **Setup Wizard**: it captures your current monitor arrangement and saves it as your first profile. You can re-run it any time (`setup` subcommand, or the "Setup Wizard" button/menu entry) to capture a fresh snapshot under a new or existing label — if a profile with that label already exists, it's backed up first, never silently overwritten with no way back.

### Arranging displays
The GUI's "Arrange..." button opens a native drag-and-drop canvas — each connected monitor is a rectangle you can drag into position, with edges snapping together to avoid gaps. **Apply** repositions your real displays immediately; **Save as Profile** persists the arrangement for later. This replaces the previous behavior of shelling out to an external `wdisplays` window; you no longer need `wdisplays` installed for this.

### Profiles: lock, backup, restore
- **Lock** a profile to protect it from accidental overwrite/rename/delete — `unlock` before changing it again.
- **Backup** takes a timestamped snapshot of a profile you can **restore** later, independent of locking.

## Legacy CLI

The original single-file script, `ezSWAYdisplay.py`, still works exactly as before (captures the current layout to one static config file, `~/.config/sway/config.d/99-display-layout.conf`, matched by connector name). It's kept for anyone relying on it directly, and its capture/backup/write logic is what the Setup Wizard above is built from — corrected there to match by hardware identity and to support multiple named profiles instead of one hardcoded file. For new setups, prefer the Setup Wizard.

## Requirements

- Sway
- Python 3, with `venv` available (`install.sh` checks and tells you clearly if not)
- `PyQt6`, `i3ipc`, `questionary` — installed automatically by `install.sh` into a local `.venv`; see `requirements.txt`

## Structure
- `ezsway/core/` — `wm_adapter.py` (Sway/Hyprland abstraction), `monitor_manager.py` + `config_store.py` (authorization policy), `profile_manager.py` + `setup_wizard.py` (saved layouts)
- `ezsway/gui/` — PyQt6 GUI: `main_window.py`, `monitor_widget.py`, `profile_panel.py`, `arrange_canvas.py`
- `ezsway/tui/` — `questionary`-based terminal UI
- `~/.config/ezSWAYdisplay/monitors.json` — authorization policy database
- `~/.config/ezSWAYdisplay/profiles/<label>.json` — saved layouts

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/
```

## License

See `LICENSE`.
