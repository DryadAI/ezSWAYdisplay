#!/bin/bash
# Installer for ezSWAYdisplay. Safe to re-run (idempotent): re-running only
# touches what's missing or broken, never duplicates a .desktop file, config
# block, or keybinding.
#
# Flags:
#   --no-keybind    skip offering a Sway keybinding for the TUI
#   --no-autostart  skip offering to add the monitor-policy autostart line
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SWAY_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/sway/config"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/ezSWAYdisplay.desktop"
MARKER_BEGIN="# >>> ezSWAYdisplay managed block >>>"
MARKER_END="# <<< ezSWAYdisplay managed block <<<"

NO_KEYBIND=0
NO_AUTOSTART=0
for arg in "$@"; do
  case "$arg" in
    --no-keybind) NO_KEYBIND=1 ;;
    --no-autostart) NO_AUTOSTART=1 ;;
  esac
done

log()  { printf '%s\n' "$*"; }
err()  { printf 'Error: %s\n' "$*" >&2; }

# -- 1. Preflight: fail clearly up front rather than half-way through --
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 not found. Install Python 3 first."
  exit 1
fi
if ! python3 -m venv --help >/dev/null 2>&1; then
  err "python3's venv module isn't available (try installing python3-venv or your distro's equivalent package)."
  exit 1
fi

# -- 2. Create or repair the venv --
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_OK=0
if [ -x "$VENV_DIR/bin/python3" ] && "$VENV_DIR/bin/python3" --version >/dev/null 2>&1; then
  VENV_OK=1
fi
if [ "$VENV_OK" -eq 0 ]; then
  if [ -d "$VENV_DIR" ]; then
    log "Existing .venv looks broken (from a previous failed install?) -- recreating it."
    rm -rf "$VENV_DIR"
  fi
  log "Creating virtual environment at $VENV_DIR..."
  python3 -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python3" -m pip --version >/dev/null 2>&1; then
  err "pip isn't available inside the venv. Something is wrong with this Python install."
  exit 1
fi

log "Installing dependencies (this needs network access)..."
"$VENV_DIR/bin/python3" -m pip install --quiet --upgrade pip || true
if ! "$VENV_DIR/bin/python3" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"; then
  err "Failed to install dependencies -- check your network connection and retry."
  err "Not installing the app launcher, since it wouldn't run yet anyway."
  exit 1
fi
log "Dependencies installed."

chmod +x "$SCRIPT_DIR/run_gui.sh" "$SCRIPT_DIR/run_tui.sh" 2>/dev/null || true
[ -f "$SCRIPT_DIR/ezSWAYdisplay.py" ] && chmod +x "$SCRIPT_DIR/ezSWAYdisplay.py" 2>/dev/null || true

# -- 3. .desktop launcher (only reached if the venv/deps step above succeeded) --
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=ezSWAYdisplay
Comment=Display manager for Sway: monitor authorization + saved layouts
Exec=$SCRIPT_DIR/run_gui.sh
Icon=video-display
Categories=System;Settings;
Terminal=false
EOF
log "Installed app-menu launcher: $DESKTOP_FILE"

# -- 4. Optional autostart line (guarded + idempotent + backed up) --
if [ "$NO_AUTOSTART" -eq 0 ]; then
  if [ ! -f "$SWAY_CONFIG" ]; then
    log "No sway config found at $SWAY_CONFIG -- skipping autostart setup (add it manually if you'd like)."
  elif grep -qF "$MARKER_BEGIN" "$SWAY_CONFIG"; then
    log "Autostart already configured (marker found in $SWAY_CONFIG) -- skipping."
  else
    read -r -p "Add ezSWAYdisplay to sway autostart, to enforce the monitor policy on login? [y/N] " ans || ans=""
    if [[ "$ans" =~ ^[Yy]$ ]]; then
      cp "$SWAY_CONFIG" "$SWAY_CONFIG.bak-$(date +%Y%m%d-%H%M%S)"
      {
        echo ""
        echo "$MARKER_BEGIN"
        echo "exec $SCRIPT_DIR/run_gui.sh"
        echo "$MARKER_END"
      } >> "$SWAY_CONFIG"
      log "Added autostart line to $SWAY_CONFIG (backup taken first)."
    else
      log "Skipped autostart -- run $SCRIPT_DIR/run_gui.sh manually whenever you like."
    fi
  fi
fi

# -- 5. Optional keybinding for the TUI, conflict-checked against existing binds --
if [ "$NO_KEYBIND" -eq 0 ] && [ -f "$SWAY_CONFIG" ]; then
  if grep -qF "$SCRIPT_DIR/run_tui.sh" "$SWAY_CONFIG" 2>/dev/null; then
    log "TUI keybinding already present -- skipping."
  else
    CONFIG_DIR="$(dirname "$SWAY_CONFIG")"
    USED_KEYS="$( { grep -rhoE '\$mod\+[A-Za-z0-9]+' "$CONFIG_DIR" 2>/dev/null || true; } | tr '[:upper:]' '[:lower:]' | sort -u)"
    FREE_KEY=""
    for k in d m o s g; do
      if ! grep -qx "\$mod+$k" <<< "$USED_KEYS"; then
        FREE_KEY="$k"
        break
      fi
    done
    if [ -n "$FREE_KEY" ]; then
      read -r -p "Add keybinding \$mod+$FREE_KEY to launch the ezSWAYdisplay TUI? [y/N] " ans || ans=""
      if [[ "$ans" =~ ^[Yy]$ ]]; then
        cp "$SWAY_CONFIG" "$SWAY_CONFIG.bak-$(date +%Y%m%d-%H%M%S)"
        echo "bindsym \$mod+$FREE_KEY exec $SCRIPT_DIR/run_tui.sh" >> "$SWAY_CONFIG"
        log "Added keybinding \$mod+$FREE_KEY -> run_tui.sh."
      fi
    else
      log "No obviously-free \$mod+<letter> keybinding found among d/m/o/s/g -- skipping (add one manually if you want)."
    fi
  fi
fi

log ""
log "Install complete. Re-running this script later is safe (idempotent)."
log "  GUI:  $SCRIPT_DIR/run_gui.sh"
log "  TUI:  $SCRIPT_DIR/run_tui.sh"
# cd + -m, not a bare script path -- ezsway/main.py uses relative imports
# ("from .core.errors import ...") and raises "ImportError: attempted
# relative import with no known parent package" if run directly, the exact
# failure run_gui.sh/run_tui.sh switched to `-m ezsway.main` to avoid. This
# printed instruction wasn't updated to match when those were fixed.
log "  CLI:  (cd $SCRIPT_DIR && $VENV_DIR/bin/python3 -m ezsway.main --help)"
