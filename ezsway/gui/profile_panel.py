from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QMessageBox, QInputDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal

from ..core.profile_manager import ProfileManager
from ..core.setup_wizard import SetupWizard
from ..core.wm_adapter import WMAdapter


class ProfilePanel(QWidget):
    """Saved-layout ("profile") management panel: load/save/rename/delete/
    lock/unlock/backup/restore, plus entry points into the Setup Wizard and
    the arrangement canvas.

    Every action here wraps its ProfileManager call in try/except and shows
    a QMessageBox with the real error text -- matching (and extending
    consistently to every button) the pattern already established by
    MonitorWidget's activate_monitor handler.

    Catches bare Exception, not just EzSwayError -- these are top-level Qt
    slots with nothing above them to catch an unexpected non-EzSwayError
    bug; see the matching comment in main_window.py.
    """
    on_open_arrange_canvas = pyqtSignal()

    def __init__(self, wm_adapter: WMAdapter, profile_manager: ProfileManager):
        super().__init__()
        self.wm = wm_adapter
        self.pm = profile_manager
        self.wizard = SetupWizard(wm_adapter, profile_manager)
        self._init_ui()
        self.refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        header = QLabel("Saved Layouts")
        header.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(header)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        row1 = QHBoxLayout()
        self.btn_load = QPushButton("Load")
        self.btn_save = QPushButton("Save As New")
        self.btn_rename = QPushButton("Rename")
        self.btn_delete = QPushButton("Delete")
        for b in (self.btn_load, self.btn_save, self.btn_rename, self.btn_delete):
            row1.addWidget(b)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_lock = QPushButton("Lock")
        self.btn_unlock = QPushButton("Unlock")
        self.btn_backup = QPushButton("Backup")
        self.btn_restore = QPushButton("Restore")
        for b in (self.btn_lock, self.btn_unlock, self.btn_backup, self.btn_restore):
            row2.addWidget(b)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        self.btn_setup = QPushButton("Setup Wizard")
        self.btn_arrange = QPushButton("Arrange...")
        self.btn_refresh = QPushButton("Refresh")
        for b in (self.btn_setup, self.btn_arrange, self.btn_refresh):
            row3.addWidget(b)
        layout.addLayout(row3)

        self.btn_load.clicked.connect(self._on_load)
        self.btn_save.clicked.connect(self._on_save_new)
        self.btn_rename.clicked.connect(self._on_rename)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_lock.clicked.connect(self._on_lock)
        self.btn_unlock.clicked.connect(self._on_unlock)
        self.btn_backup.clicked.connect(self._on_backup)
        self.btn_restore.clicked.connect(self._on_restore)
        self.btn_setup.clicked.connect(self._on_setup_wizard)
        self.btn_arrange.clicked.connect(self.on_open_arrange_canvas.emit)
        self.btn_refresh.clicked.connect(self.refresh)

    def refresh(self):
        self.list_widget.clear()
        for p in self.pm.list_profiles():
            flags = []
            if p["active"]:
                flags.append("active")
            if p["locked"]:
                flags.append("locked")
            flag_str = f"  [{', '.join(flags)}]" if flags else ""
            item = QListWidgetItem(f"{p['label']}{flag_str}")
            item.setData(Qt.ItemDataRole.UserRole, p["label"])
            self.list_widget.addItem(item)

    def _selected_label(self):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.information(self, "No selection", "Select a profile first.")
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_load(self):
        label = self._selected_label()
        if not label:
            return
        try:
            result = self.pm.load_profile(label)
            if result.ok:
                msg = f"Loaded {label!r} ({len(result.applied)} output(s) applied)."
                if result.skipped_not_connected:
                    msg += f"\nSkipped (not connected): {', '.join(result.skipped_not_connected)}"
                QMessageBox.information(self, "Loaded", msg)
            else:
                details = "\n".join(f"{f['unique_id']}: {f['error']}" for f in result.failed)
                QMessageBox.warning(
                    self, "Partially applied",
                    f"{label!r} applied with {len(result.failed)} failure(s):\n{details}"
                )
        except Exception as e:
            QMessageBox.critical(self, "Load failed", str(e))
        self.refresh()

    def _on_save_new(self):
        label, ok = QInputDialog.getText(self, "Save current layout", "Label:")
        if not ok or not label:
            return
        try:
            self.pm.save_profile(label, self.wm.get_outputs())
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
        self.refresh()

    def _on_rename(self):
        old = self._selected_label()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Rename profile", "New label:", text=old)
        if not ok or not new:
            return
        try:
            self.pm.rename_profile(old, new)
        except Exception as e:
            QMessageBox.critical(self, "Rename failed", str(e))
        self.refresh()

    def _on_delete(self):
        label = self._selected_label()
        if not label:
            return
        if QMessageBox.question(
            self, "Confirm delete", f"Really delete profile {label!r}?"
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.pm.remove_profile(label)
        except Exception as e:
            QMessageBox.critical(self, "Delete failed", str(e))
        self.refresh()

    def _on_lock(self):
        label = self._selected_label()
        if not label:
            return
        try:
            self.pm.lock_profile(label)
        except Exception as e:
            QMessageBox.critical(self, "Lock failed", str(e))
        self.refresh()

    def _on_unlock(self):
        label = self._selected_label()
        if not label:
            return
        try:
            self.pm.unlock_profile(label)
        except Exception as e:
            QMessageBox.critical(self, "Unlock failed", str(e))
        self.refresh()

    def _on_backup(self):
        label = self._selected_label()
        if not label:
            return
        try:
            backup_id = self.pm.backup_profile(label)
            QMessageBox.information(self, "Backed up", f"Saved as {backup_id}")
        except Exception as e:
            QMessageBox.critical(self, "Backup failed", str(e))

    def _on_restore(self):
        backups = self.pm.list_backups()
        if not backups:
            QMessageBox.information(self, "No backups", "There are no backups yet.")
            return
        backup_id, ok = QInputDialog.getItem(
            self, "Restore backup", "Choose a backup:", backups, editable=False
        )
        if not ok or not backup_id:
            return
        try:
            restored_label = self.pm.restore_backup(backup_id)
            QMessageBox.information(self, "Restored", f"Restored -> profile {restored_label!r}")
        except Exception as e:
            QMessageBox.critical(self, "Restore failed", str(e))
        self.refresh()

    def _on_setup_wizard(self):
        label, ok = QInputDialog.getText(
            self, "Setup Wizard", "Label for current layout (leave blank for auto):"
        )
        if not ok:
            return
        try:
            saved_label = self.wizard.run(label=label or None)
            QMessageBox.information(self, "Setup complete", f"Saved current layout as {saved_label!r}.")
        except Exception as e:
            QMessageBox.critical(self, "Setup failed", str(e))
        self.refresh()
