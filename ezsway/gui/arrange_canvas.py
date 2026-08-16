"""Native drag-and-drop monitor arrangement canvas.

Replaces the previous behavior of shelling out to an external `wdisplays`
process (main_window.py's old configure_monitor()) with an in-app
QGraphicsView/QGraphicsScene canvas, matching the interaction model of
Garuda's default Sway arrangement tools (nwg-displays/wdisplays): each
monitor is a draggable, labeled rectangle; dropping near another monitor's
edge snaps to it.

Dragging is purely visual/in-memory until Apply or Save as Profile is
clicked -- nothing touches real outputs until then.
"""
import logging
from typing import Dict

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPen
from PyQt6.QtWidgets import (
    QDialog, QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QInputDialog,
    QMessageBox, QPushButton, QVBoxLayout,
)

from ..core.errors import EzSwayError, WMCommandError
from ..core.profile_manager import ProfileManager, verify_output_state
from ..core.wm_adapter import Monitor, WMAdapter

logger = logging.getLogger(__name__)

_CANVAS_SCALE = 0.08  # real pixels -> canvas units
_SNAP_THRESHOLD = 12  # canvas units
_MIN_RECT_SIZE = 60


class _MonitorRectItem(QGraphicsRectItem):
    """A draggable rectangle representing one monitor. Snaps its edges to any
    other item's edge on release, within _SNAP_THRESHOLD."""

    def __init__(self, unique_id: str, width: float, height: float, canvas: "ArrangeCanvas"):
        super().__init__(0, 0, width, height)
        self.unique_id = unique_id
        self.canvas = canvas
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setPen(QPen(QColor("#4a90d9"), 2))
        self.setBrush(QBrush(QColor("#2b3a4a")))

    def mark_apply_failed(self):
        self.setPen(QPen(QColor("#d94a4a"), 3))

    def clear_apply_failed(self):
        self.setPen(QPen(QColor("#4a90d9"), 2))

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.canvas.snap_item(self)


class ArrangeCanvas(QDialog):
    def __init__(self, wm_adapter: WMAdapter, profile_manager: ProfileManager, parent=None):
        super().__init__(parent)
        self.wm = wm_adapter
        self.pm = profile_manager
        self.setWindowTitle("Arrange Displays")
        self.resize(900, 600)

        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)

        layout = QVBoxLayout(self)
        layout.addWidget(self.view)

        button_row = QHBoxLayout()
        self.btn_apply = QPushButton("Apply")
        self.btn_save = QPushButton("Save as Profile")
        self.btn_refresh = QPushButton("Refresh")
        self.btn_close = QPushButton("Close")
        for b in (self.btn_apply, self.btn_save, self.btn_refresh, self.btn_close):
            button_row.addWidget(b)
        layout.addLayout(button_row)

        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_refresh.clicked.connect(self._load_monitors)
        self.btn_close.clicked.connect(self.close)

        self._items: Dict[str, _MonitorRectItem] = {}
        self._monitors: Dict[str, Monitor] = {}
        self._load_monitors()

    def _load_monitors(self):
        self.scene.clear()
        self._items.clear()
        try:
            monitors = self.wm.get_outputs()
        except WMCommandError as e:
            QMessageBox.critical(self, "Cannot query displays", str(e))
            return
        self._monitors = {m.unique_id: m for m in monitors}

        for m in monitors:
            w = max(m.width * m.scale * _CANVAS_SCALE, _MIN_RECT_SIZE)
            h = max(m.height * m.scale * _CANVAS_SCALE, _MIN_RECT_SIZE)
            item = _MonitorRectItem(m.unique_id, w, h, self)
            item.setPos(m.pos_x * _CANVAS_SCALE, m.pos_y * _CANVAS_SCALE)
            self.scene.addItem(item)

            label = QGraphicsSimpleTextItem(
                f"{m.name}\n{int(m.width)}x{int(m.height)}"
                + ("" if m.active else "\n(disabled)"),
                item,
            )
            label.setPos(4, 4)

            self._items[m.unique_id] = item

        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-100, -100, 100, 100))

    def snap_item(self, moved: _MonitorRectItem):
        """Snap `moved`'s edges to the nearest edge of any other item, within
        _SNAP_THRESHOLD canvas units, to avoid tiny visible gaps/overlaps."""
        moved_rect = moved.sceneBoundingRect()
        best_dx, best_dy = None, None

        for uid, other in self._items.items():
            if other is moved:
                continue
            other_rect = other.sceneBoundingRect()

            # Horizontal snap: moved's left edge to other's right edge, or vice versa
            for a, b in ((moved_rect.left(), other_rect.right()), (moved_rect.right(), other_rect.left())):
                dx = b - a
                if abs(dx) <= _SNAP_THRESHOLD and (best_dx is None or abs(dx) < abs(best_dx)):
                    best_dx = dx

            # Vertical snap: moved's top edge to other's bottom edge, or vice versa
            for a, b in ((moved_rect.top(), other_rect.bottom()), (moved_rect.bottom(), other_rect.top())):
                dy = b - a
                if abs(dy) <= _SNAP_THRESHOLD and (best_dy is None or abs(dy) < abs(best_dy)):
                    best_dy = dy

        if best_dx is not None or best_dy is not None:
            offset = QPointF(best_dx or 0, best_dy or 0)
            moved.setPos(moved.pos() + offset)

    def _current_positions(self):
        """Reads back canvas positions (canvas units) -> real pixel positions."""
        positions = {}
        for uid, item in self._items.items():
            pos = item.pos()
            positions[uid] = (
                round(pos.x() / _CANVAS_SCALE),
                round(pos.y() / _CANVAS_SCALE),
            )
        return positions

    def _on_apply(self):
        positions = self._current_positions()
        any_failed = False
        for uid, (x, y) in positions.items():
            item = self._items[uid]
            m = self._monitors[uid]
            item.clear_apply_failed()
            if not m.active:
                continue
            mode = f"{int(m.width)}x{int(m.height)}"
            position = f"{x} {y}"
            try:
                self.wm.enable_output(
                    m.name,
                    mode=mode,
                    position=position,
                    scale=m.scale,
                    transform=m.transform,
                )
            except (WMCommandError, ValueError) as e:
                logger.warning("Failed to apply position for %s: %s", uid, e)
                item.mark_apply_failed()
                any_failed = True
                continue

            # Re-query and confirm the change actually took effect -- this
            # reimplemented "call enable_output" without the same
            # verification profile_manager.load_profile() has (a sway
            # "success: true" reply doesn't guarantee the output actually
            # moved), so a drag-and-drop Apply could report success while
            # the layout silently didn't change.
            if not verify_output_state(self.wm, uid, want_wh=mode, want_pos=position):
                logger.warning("Position for %s was accepted but not verified applied", uid)
                item.mark_apply_failed()
                any_failed = True

        if any_failed:
            QMessageBox.warning(
                self, "Partially applied",
                "Some outputs did not apply -- they're outlined in red on the canvas.",
            )
        else:
            QMessageBox.information(self, "Applied", "Layout applied.")

    def _on_save(self):
        label, ok = QInputDialog.getText(self, "Save as Profile", "Label:")
        if not ok or not label:
            return
        positions = self._current_positions()
        for uid, (x, y) in positions.items():
            m = self._monitors[uid]
            m.pos_x, m.pos_y = x, y
        try:
            self.pm.save_profile(label, list(self._monitors.values()))
            QMessageBox.information(self, "Saved", f"Saved as {label!r}.")
        except EzSwayError as e:
            QMessageBox.critical(self, "Save failed", str(e))
