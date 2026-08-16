from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QScrollArea,
                             QPushButton, QLabel, QHBoxLayout, QMessageBox,
                             QTabWidget)
from PyQt6.QtCore import QTimer
import sys
from ..core.errors import EzSwayError
from ..core.monitor_manager import MonitorManager
from ..core.profile_manager import ProfileManager
from .monitor_widget import MonitorWidget
from .profile_panel import ProfilePanel
from .arrange_canvas import ArrangeCanvas

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ezSWAYdisplay Manager")
        self.resize(700, 500)

        self.manager = MonitorManager()
        self.profile_manager = ProfileManager(self.manager.wm)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        # -- "Displays" tab: existing per-monitor authorization policy --
        displays_tab = QWidget()
        self.main_layout = QVBoxLayout(displays_tab)

        header_layout = QHBoxLayout()
        header_label = QLabel("Connected Displays")
        header_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        header_layout.addWidget(header_label)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_list)
        header_layout.addWidget(btn_refresh)

        self.main_layout.addLayout(header_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_area.setWidget(self.scroll_content)

        self.main_layout.addWidget(self.scroll_area)
        tabs.addTab(displays_tab, "Displays")

        # -- "Saved Layouts" tab: new profile management --
        self.profile_panel = ProfilePanel(self.manager.wm, self.profile_manager)
        self.profile_panel.on_open_arrange_canvas.connect(self.open_arrange_canvas)
        tabs.addTab(self.profile_panel, "Saved Layouts")

        self.run_policy()

        # Timer for auto-refresh/check (every 5 seconds)
        # Known limitation (tracked, not fixed here): this does blocking I/O
        # (subprocess/IPC) directly on the Qt main thread. A hung WM IPC
        # connection will freeze the GUI. A real fix means moving
        # refresh_monitors() onto a QThread worker -- bigger structural
        # change than this pass covers; flagging explicitly rather than
        # pretending this is fine.
        self.timer = QTimer()
        self.timer.timeout.connect(self.check_updates)
        self.timer.start(5000)

    def open_arrange_canvas(self):
        dialog = ArrangeCanvas(self.manager.wm, self.profile_manager, parent=self)
        dialog.exec()
        self.refresh_list()
        self.profile_panel.refresh()

    def run_policy(self):
        try:
            self.manager.enforce_policy()
            self.refresh_list()
        except EzSwayError as e:
            QMessageBox.critical(self, "Error", f"Failed to enforce policy: {e}")

    def check_updates(self):
        try:
            self.refresh_list(enforce=False)
        except EzSwayError as e:
            # Don't pop a dialog every 5s on a persistent WM issue -- log and
            # let the user notice via the (now-stale) display list, same as
            # before this hardening pass, but at least not silently `pass`.
            print(f"Periodic refresh failed: {e}", file=sys.stderr)

    def refresh_list(self, enforce=True):
        if enforce:
            self.manager.enforce_policy()  # Detects new monitors and disables them if unknown

        self.manager.refresh_monitors()

        # Clear existing (deleteLater(), not just setParent(None), to avoid
        # leaking Qt objects over a long-running session with this 5s timer).
        for i in reversed(range(self.scroll_layout.count())):
            widget = self.scroll_layout.itemAt(i).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        # Populate
        for m in self.manager.monitors:
            is_known = self.manager.config_store.is_known(m.unique_id)
            w = MonitorWidget(m, is_known)
            w.on_activate.connect(self.activate_monitor)
            w.on_configure.connect(self.configure_monitor)
            w.on_deactivate.connect(self.deactivate_monitor)
            self.scroll_layout.addWidget(w)

        self.scroll_layout.addStretch()

    def activate_monitor(self, unique_id):
        try:
            self.manager.activate_monitor(unique_id)
            self.refresh_list()
        except EzSwayError as e:
            QMessageBox.warning(self, "Error", f"Failed to activate: {e}")

    def deactivate_monitor(self, unique_id):
        try:
            self.manager.deactivate_monitor(unique_id)
            self.refresh_list()
        except EzSwayError as e:
            QMessageBox.warning(self, "Error", f"Failed to deactivate: {e}")

    def configure_monitor(self, unique_id):
        """Opens the native drag-and-drop arrangement canvas.

        Previously this shelled out to an external `wdisplays` process (or
        showed a plain text dump of the saved config if wdisplays wasn't
        installed) -- now the app does this natively instead of delegating
        to another GTK tool for the one thing it should do itself.
        """
        self.open_arrange_canvas()
