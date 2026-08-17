"""Terminal equivalent of the GUI's drag-and-drop ArrangeCanvas.

curses can't drag with a mouse, so arrow keys move the selected monitor
instead -- everything else (edge-snapping, apply-with-verify, save-as-
profile) mirrors ezsway/gui/arrange_canvas.py exactly, sharing the same
verify_output_state() the rest of the app uses to catch "IPC said success
but nothing actually moved."

The geometry/apply logic below is plain functions with no curses
dependency, so it's unit-testable the same way as the rest of core/ --
only run_arrange() itself needs a real terminal.
"""
import curses
from typing import Dict, List, Tuple

from ..core.errors import EzSwayError
from ..core.profile_manager import ProfileManager, verify_output_state
from ..core.wm_adapter import Monitor, WMAdapter

_STEP_SIZES = (10, 40, 160)
# Must be strictly less than the smallest step size above. Found live: with
# threshold == the default step (40px), a monitor sitting flush against a
# neighbor (the normal case for an adjacent layout, not an edge case) could
# never actually move -- every keypress moved it exactly `step` pixels away,
# which lands exactly at the snap threshold, and (>=/<= being inclusive)
# immediately snapped it right back to where it started. Every single
# keypress silently no-op'd; nothing was wrong with key handling at all.
_SNAP_THRESHOLD = 8


def compute_snap(moved_uid: str, positions: Dict[str, Tuple[int, int]],
                  sizes: Dict[str, Tuple[int, int]], threshold: int = _SNAP_THRESHOLD) -> Tuple[int, int]:
    """Returns the (dx, dy) offset that snaps `moved`'s edges to the nearest
    edge of any other monitor's rect, within `threshold` pixels on each axis
    independently -- same rule as ArrangeCanvas.snap_item(), just expressed
    on plain (x, y)/(w, h) dicts instead of QGraphicsRectItems."""
    mx, my = positions[moved_uid]
    mw, mh = sizes[moved_uid]
    best_dx, best_dy = None, None

    for uid, (ox, oy) in positions.items():
        if uid == moved_uid:
            continue
        ow, oh = sizes[uid]
        for a, b in ((mx, ox + ow), (mx + mw, ox)):
            dx = b - a
            if abs(dx) <= threshold and (best_dx is None or abs(dx) < abs(best_dx)):
                best_dx = dx
        for a, b in ((my, oy + oh), (my + mh, oy)):
            dy = b - a
            if abs(dy) <= threshold and (best_dy is None or abs(dy) < abs(best_dy)):
                best_dy = dy

    return (best_dx or 0, best_dy or 0)


def apply_positions(wm: WMAdapter, monitors: Dict[str, Monitor],
                     positions: Dict[str, Tuple[int, int]]) -> Tuple[List[str], List[str], List[str]]:
    """Applies new positions to every ACTIVE monitor (a disabled output has
    no meaningful on-screen position to send to the WM -- same skip rule as
    ArrangeCanvas._on_apply()), verifying each one actually moved. Returns
    (applied, skipped_inactive, failed) unique_ids/names."""
    applied, skipped_inactive, failed = [], [], []
    for uid, (x, y) in positions.items():
        m = monitors[uid]
        if not m.active:
            skipped_inactive.append(m.name)
            continue
        mode = f"{int(m.width)}x{int(m.height)}"
        position = f"{x} {y}"
        try:
            wm.enable_output(m.name, mode=mode, position=position, scale=m.scale, transform=m.transform)
        except EzSwayError:
            failed.append(m.name)
            continue
        if verify_output_state(wm, uid, want_wh=mode, want_pos=position):
            applied.append(m.name)
        else:
            failed.append(m.name)
    return applied, skipped_inactive, failed


def _fit_scale(positions: Dict[str, Tuple[int, int]], sizes: Dict[str, Tuple[int, int]],
                avail_w: int, avail_h: int) -> float:
    """Picks a scale (real pixels -> terminal columns) so the full layout's
    bounding box fits the available drawing area, leaving margin. Character
    cells are roughly twice as tall as wide, so the vertical axis is scaled
    down further (by ROW_ASPECT) when converting to curses rows -- applied
    by the caller, not baked in here, since this returns a single pixel ->
    column scale shared by both axes (keeps monitor aspect ratios correct
    on screen)."""
    if not positions:
        return 0.02
    xs = [x for x, _ in positions.values()]
    ys = [y for _, y in positions.values()]
    max_x = max(x + sizes[uid][0] for uid, (x, _) in positions.items())
    max_y = max(y + sizes[uid][1] for uid, (_, y) in positions.items())
    min_x, min_y = min(xs), min(ys)
    bbox_w = max(max_x - min_x, 1)
    bbox_h = max(max_y - min_y, 1)
    scale_w = (avail_w - 4) / bbox_w
    scale_h = (avail_h - 4) / bbox_h
    return max(min(scale_w, scale_h), 0.002)


ROW_ASPECT = 0.5  # terminal rows are ~2x taller than columns are wide


def run_arrange(wm: WMAdapter, pm: ProfileManager):
    """Entry point -- wraps the interactive curses session. Falls back to a
    clean printed error (not a raw traceback) if the WM can't be reached,
    same as every other TUI action."""
    try:
        monitors_list = wm.get_outputs()
    except EzSwayError as e:
        print(f"\n✗ Cannot query displays: {e}\n")
        return
    if not monitors_list:
        print("\nNo displays detected.\n")
        return

    monitors: Dict[str, Monitor] = {m.unique_id: m for m in monitors_list}
    positions = {uid: (m.pos_x, m.pos_y) for uid, m in monitors.items()}
    sizes = {uid: (int(m.width * m.scale) or 1, int(m.height * m.scale) or 1) for uid, m in monitors.items()}
    order = list(monitors.keys())

    result = curses.wrapper(_curses_loop, wm, pm, monitors, positions, sizes, order)
    if result:
        print(f"\n{result}\n")


def _curses_loop(stdscr, wm, pm, monitors, positions, sizes, order):
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.keypad(True)
    selected = 0
    step_idx = 1
    # hjkl (vi-style) are the primary movement keys, not arrows: curses is
    # entered *after* questionary/prompt_toolkit has already run in this
    # same process, and prompt_toolkit's terminal-mode handoff was found
    # (live, via a tmux-driven key-by-key test) to break ncurses' escape-
    # sequence timing/assembly for multi-byte arrow keys specifically --
    # single ASCII keys (like 'q', already relied on for quit) aren't
    # affected. Arrow keys are still handled as a bonus for terminals/
    # setups where they do work, but hjkl is what's documented and
    # guaranteed.
    status = "hjkl move (arrows may also work) -- Tab select -- [/] step size -- a apply -- w save -- q quit"

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        draw_h = max_y - 2
        scale = _fit_scale(positions, sizes, max_x, int(draw_h / ROW_ASPECT))
        xs = [x for x, _ in positions.values()]
        ys = [y for _, y in positions.values()]
        min_x, min_y = min(xs), min(ys)

        for i, uid in enumerate(order):
            x, y = positions[uid]
            w, h = sizes[uid]
            col = int((x - min_x) * scale) + 2
            row = int((y - min_y) * scale * ROW_ASPECT) + 1
            width = max(int(w * scale), 6)
            height = max(int(h * scale * ROW_ASPECT), 3)
            attr = curses.A_REVERSE if i == selected else curses.A_NORMAL
            _draw_box(stdscr, row, col, height, width, monitors[uid].name, attr, max_y, max_x)

        stdscr.addstr(max_y - 1, 0, (status + f" -- step {_STEP_SIZES[step_idx]}px")[:max_x - 1])
        stdscr.refresh()

        key = stdscr.getch()
        uid = order[selected]
        step = _STEP_SIZES[step_idx]
        if key in (curses.KEY_LEFT, ord('h')):
            positions[uid] = (positions[uid][0] - step, positions[uid][1])
        elif key in (curses.KEY_RIGHT, ord('l')):
            positions[uid] = (positions[uid][0] + step, positions[uid][1])
        elif key in (curses.KEY_UP, ord('k')):
            positions[uid] = (positions[uid][0], positions[uid][1] - step)
        elif key in (curses.KEY_DOWN, ord('j')):
            positions[uid] = (positions[uid][0], positions[uid][1] + step)
        elif key == ord('\t'):
            selected = (selected + 1) % len(order)
            continue
        elif key == ord('['):
            step_idx = max(step_idx - 1, 0)
            continue
        elif key == ord(']'):
            step_idx = min(step_idx + 1, len(_STEP_SIZES) - 1)
            continue
        elif key == ord('q') or key == 27:
            return None
        elif key == ord('a'):
            applied, skipped, failed = apply_positions(wm, monitors, positions)
            msg = f"Applied: {', '.join(applied) or '(none)'}"
            if skipped:
                msg += f" -- skipped (disabled): {', '.join(skipped)}"
            if failed:
                msg += f" -- FAILED: {', '.join(failed)}"
            return msg
        elif key == ord('w'):
            label = _prompt_line(stdscr, max_y, "Save as label: ")
            if label:
                for u in order:
                    m = monitors[u]
                    m.pos_x, m.pos_y = positions[u]
                try:
                    pm.save_profile(label, list(monitors.values()))
                    return f"Saved as {label!r}."
                except EzSwayError as e:
                    return f"Save failed: {e}"
            continue

        dx, dy = compute_snap(uid, positions, sizes)
        positions[uid] = (positions[uid][0] + dx, positions[uid][1] + dy)


def _draw_box(stdscr, row, col, height, width, label, attr, max_y, max_x):
    if row < 0 or col < 0 or row >= max_y or col >= max_x:
        return
    height = min(height, max_y - row - 1)
    width = min(width, max_x - col - 1)
    if height < 1 or width < 1:
        return
    try:
        for r in range(height):
            stdscr.addstr(row + r, col, " " * width, attr)
        stdscr.addstr(row, col, label[:width], attr | curses.A_BOLD)
    except curses.error:
        pass  # writing to the terminal's bottom-right cell raises in curses; harmless


def _prompt_line(stdscr, max_y, prompt: str) -> str:
    curses.echo()
    curses.curs_set(1)
    stdscr.addstr(max_y - 1, 0, prompt)
    stdscr.clrtoeol()
    try:
        value = stdscr.getstr(max_y - 1, len(prompt), 60).decode("utf-8", "ignore").strip()
    finally:
        curses.noecho()
        curses.curs_set(0)
    return value
