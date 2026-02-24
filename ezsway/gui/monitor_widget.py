from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QFrame, QStyle)
from PyQt6.QtCore import pyqtSignal, Qt
from ..core.wm_adapter import Monitor

class MonitorWidget(QFrame):
    """
    Widget representing a single monitor.
    Displays: Name, Model, Resolution, Status.
    Actions: Activate, Deactivate, Configure.
    """
    on_activate = pyqtSignal(str) # unique_id
    on_deactivate = pyqtSignal(str) # unique_id
    on_configure = pyqtSignal(str) # unique_id

    def __init__(self, monitor: Monitor, is_known: bool):
        super().__init__()
        self.monitor = monitor
        self.is_known = is_known
        self.init_ui()

    def init_ui(self):
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setLineWidth(2)
        
        layout = QHBoxLayout()
        self.setLayout(layout)

        # Icon/Status
        status_color = "green" if self.monitor.active else "red"
        if not self.is_known and not self.monitor.active:
            status_color = "gray" # New/Disabled
        
        status_indicator = QLabel()
        status_indicator.setFixedSize(16, 16)
        status_indicator.setStyleSheet(f"background-color: {status_color}; border-radius: 8px;")
        layout.addWidget(status_indicator)

        # Info
        info_layout = QVBoxLayout()
        conn_name = QLabel(f"<b>{self.monitor.name}</b>")
        model_name = QLabel(f"{self.monitor.make} {self.monitor.model}")
        res_info = QLabel(f"{int(self.monitor.width)}x{int(self.monitor.height)} @ {self.monitor.refresh_rate:.2f}Hz")
        
        info_layout.addWidget(conn_name)
        info_layout.addWidget(model_name)
        info_layout.addWidget(res_info)
        layout.addLayout(info_layout)
        
        layout.addStretch()

        # Controls
        controls_layout = QVBoxLayout()
        
        if not self.monitor.active:
            btn_activate = QPushButton("Activate")
            btn_activate.clicked.connect(lambda: self.on_activate.emit(self.monitor.unique_id))
            controls_layout.addWidget(btn_activate)
        else:
            btn_deactivate = QPushButton("Disable")
            btn_deactivate.clicked.connect(lambda: self.on_deactivate.emit(self.monitor.unique_id))
            controls_layout.addWidget(btn_deactivate)

        btn_configure = QPushButton("Configure")
        btn_configure.clicked.connect(lambda: self.on_configure.emit(self.monitor.unique_id))
        controls_layout.addWidget(btn_configure)

        layout.addLayout(controls_layout)

    def update_state(self, monitor: Monitor, is_known: bool):
        """Update widget state if monitor changed."""
        self.monitor = monitor
        self.is_known = is_known
        # Rebuild UI or just update labels? For now, we are recreating list mostly.
        # But if we update in place:
        # TODO: Optimize updates.
