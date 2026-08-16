from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QScrollArea,
                             QPushButton, QLabel, QHBoxLayout, QMessageBox,
                             QTabWidget)
from PyQt6.QtCore import QTimer
import sys
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

        # Deferred to right after the event loop starts (and the window has
        # painted) instead of calling it synchronously here -- this used to
        # block the *first paint* of the window on WM IPC, a stronger version
        # of the already-documented 5s-timer blocking-I/O limitation below,
        # but happening before the user sees anything at all.
        QTimer.singleShot(0, self.run_policy)

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

    # Every handler below that's directly connected to a Qt signal (button
    # click, timer, dialog-close) catches bare Exception, not just
    # EzSwayError. These are top-level from Qt's perspective -- nothing in
    # this codebase sits above a slot to catch what escapes it, so an
    # unexpected non-EzSwayError bug (an AttributeError three layers down in
    # a WM adapter, say) would otherwise propagate out of the slot silently
    # instead of showing the dialog this hardening pass exists to guarantee.
    # This was originally only done for run_policy() (the auto-run-on-
    # startup handler); a later review round pointed out the same reasoning
    # applies to every other slot, not just that one -- narrowing any of
    # them to EzSwayError-only was leaving the same gap run_policy's own
    # comment already explained, just not yet applied consistently.

    def open_arrange_canvas(self):
        dialog = ArrangeCanvas(self.manager.wm, self.profile_manager, parent=self)
        dialog.exec()
        try:
            self.refresh_list()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to refresh the display list: {e}")
        self.profile_panel.refresh()

    def run_policy(self):
        try:
            self.manager.enforce_policy()
            self.refresh_list()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to enforce policy: {e}")

    def check_updates(self):
        try:
            self.refresh_list(enforce=False)
        except Exception as e:
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
        # Mutation and refresh are wrapped separately -- previously one
        # try/except covered both, so a failure in the *refresh* that
        # happened after a *successful* activation was misreported as
        # "Failed to activate", potentially prompting a confusing retry that
        # double-toggles state which had already changed.
        try:
            self.manager.activate_monitor(unique_id)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to activate: {e}")
            return
        try:
            self.refresh_list()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Activated, but failed to refresh the display list: {e}")

    def deactivate_monitor(self, unique_id):
        try:
            self.manager.deactivate_monitor(unique_id)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to deactivate: {e}")
            return
        try:
            self.refresh_list()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Deactivated, but failed to refresh the display list: {e}")

    def configure_monitor(self, unique_id):
        """Opens the native drag-and-drop arrangement canvas.

        Previously this shelled out to an external `wdisplays` process (or
        showed a plain text dump of the saved config if wdisplays wasn't
        installed) -- now the app does this natively instead of delegating
        to another GTK tool for the one thing it should do itself.
        """
        self.open_arrange_canvas()
