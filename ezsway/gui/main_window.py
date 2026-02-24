from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QScrollArea, 
                             QPushButton, QLabel, QHBoxLayout, QMessageBox)
from PyQt6.QtCore import QTimer
import sys
from ..core.monitor_manager import MonitorManager
from .monitor_widget import MonitorWidget

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ezSWAYdisplay Manager")
        self.resize(600, 400)
        
        self.manager = MonitorManager()
        
        # Central Widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)

        # Header
        header_layout = QHBoxLayout()
        header_label = QLabel("Connected Displays")
        header_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        header_layout.addWidget(header_label)
        
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_list)
        header_layout.addWidget(btn_refresh)
        
        self.main_layout.addLayout(header_layout)

        # Monitor List Area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_area.setWidget(self.scroll_content)
        
        self.main_layout.addWidget(self.scroll_area)
        
        # Enforce Policy on Start?
        # Maybe we should ask user if they want to enable background enforcement?
        # For now, let's just refresh list, and maybe run policy. 
        # CAUTION: Running policy immediately might be aggressive.
        # But per user request "one app that ... just disables them".
        # So yes, we should run policy.
        self.run_policy()

        # Timer for auto-refresh/check (every 5 seconds)
        self.timer = QTimer()
        self.timer.timeout.connect(self.check_updates)
        self.timer.start(5000)

    def run_policy(self):
        try:
            self.manager.enforce_policy()
            self.refresh_list()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to enforce policy: {e}")

    def check_updates(self):
        # TODO: Check if monitor count changed without full refresh?
        # For now, just run policy (which effectively refreshes and enforces)
        # We might want to be less aggressive if user is in middle of editing.
        pass
        # self.run_policy() # Maybe too aggressive to do every 5s if standard usage?
        # Let's just refresh list for status updates.
        self.refresh_list(enforce=False)

    def refresh_list(self, enforce=True):
        if enforce:
            self.manager.enforce_policy() # Detects new monitors and disables them if unknown
            
        self.manager.refresh_monitors()
        
        # Clear existing
        for i in reversed(range(self.scroll_layout.count())): 
            self.scroll_layout.itemAt(i).widget().setParent(None)

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
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to activate: {e}")

    def deactivate_monitor(self, unique_id):
        self.manager.deactivate_monitor(unique_id)
        self.refresh_list()

    def configure_monitor(self, unique_id):
        # Try to launch wdisplays or similar tool
        import shutil
        import subprocess
        
        tool = shutil.which("wdisplays")
        if tool:
            try:
                subprocess.Popen([tool])
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to launch configuration tool: {e}")
        else:
            # Fallback
            target = self.manager.config_store.get_monitor_config(unique_id)
            QMessageBox.information(self, "Configuration", 
                                  f"No configuration tool (wdisplays) found.\n\nCurrent Config for {unique_id}:\n{target}\n\nPlease install wdisplays for GUI configuration.")

