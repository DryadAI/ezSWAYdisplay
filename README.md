# ezSWAYdisplay

**A GUI-powered Display Manager for Wayland (Sway/Hyprland)**

Originally a CLI tool, `ezSWAYdisplay` has been evolved into a full GUI application that provides strict control over your multi-monitor setup.

## Key Features

- **Default Deny Policy**: New monitors are disabled by default until you explicitly activate them. No more windows jumping to random screens on plug-in.
- **Fail-Safe Protection**: Ensures you never get locked out by always keeping at least one monitor active.
- **Window Manager Agnostic**: Currently supports Sway (via IPC) and has architecture for Hyprland.
- **Persistent Configuration**: Remembers which monitors you have authorized.

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/NModlin/ezSWAYdisplay.git
   cd ezSWAYdisplay
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   *Note: Requires `PyQt6` and `i3ipc`.*

   For configuration dialogs, we recommend installing `wdisplays`:
   ```bash
   sudo pacman -S wdisplays  # Arch/Garuda
   # or
   sudo apt install wdisplays # Debian/Ubuntu
   ```

## Usage

### GUI Application
Run the GUI manager:
```bash
./run_gui.sh
```

### Auto-Start (Recommended)
 To enforce the monitor policy on login, add this to your Sway config (`~/.config/sway/config` or `~/.config/sway/config.d/99-autostart.conf`):
```config
exec /path/to/ezSWAYdisplay/run_gui.sh
```

## Legacy CLI
The original CLI script is still available as `ezSWAYdisplay.py` for those who prefer the static config file generation method.

## Structure
- `ezsway/`: Main application package
- `~/.config/ezSWAYdisplay/monitors.json`: Database of known monitors.
