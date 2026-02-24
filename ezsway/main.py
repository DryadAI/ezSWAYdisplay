#!/usr/bin/env python3
import sys
import logging
from PyQt6.QtWidgets import QApplication
from ezsway.gui.main_window import MainWindow

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ezSWAYdisplay")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
