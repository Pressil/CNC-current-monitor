import sys
import time
import datetime
import numpy as np

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QMessageBox)
from PyQt6.QtCore import QTimer, Qt

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

# Import the DAQ backend engine from backend.py
from backend import DAQEngine, SAMPLES # pyright: ignore[reportMissingImports]

class CNCApp(QMainWindow):
    def __init__(self):
        
        super().__init__()
        self.setWindowTitle("CNC Current Monitor")
        self.resize(1000, 700)
        
        # Instantiate the hardware backend engine
        self.engine = DAQEngine()
        self.session_counter = 0

        # Clean Light Theme Styling
        self.setStyleSheet("""
            QMainWindow { background-color: #f5f5f5; }
            QLabel { color: #333333; font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; }
            QPushButton { 
                font-family: 'Segoe UI', Arial, sans-serif; 
                border-radius: 6px; 
                border: none;
                font-weight: bold;
            }
            QPushButton:disabled { background-color: #cccccc; color: #888888; }
        """)

        # --- CENTRAL WIDGET & LAYOUT ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # --- TOP HEADER BAR ---
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setSpacing(12)
        
        self.start_btn = QPushButton("START")
        self.start_btn.setFixedSize(90, 36)
        self.start_btn.setStyleSheet("font-size: 10pt; background-color: #4CAF50; color: white;")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.clicked.connect(self.start_logging)
        top_bar_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setFixedSize(90, 36)
        self.stop_btn.setStyleSheet("font-size: 10pt; background-color: #f44336; color: white;")
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.clicked.connect(self.stop_logging)
        self.stop_btn.setEnabled(False) 
        top_bar_layout.addWidget(self.stop_btn)
        
        top_bar_layout.addStretch() 

        # Diagnostics & Status Labels
        self.connection_status_label = QLabel("Status: Disconnected")
        self.connection_status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #d32f2f; margin-right: 10px;")
        top_bar_layout.addWidget(self.connection_status_label)

        self.instant_label = QLabel("RMS: 0.0000 A  |  Peak: 0.0000 A")
        self.instant_label.setStyleSheet("font-size: 11pt; font-weight: bold; color: #1976d2; margin-right: 10px;")
        top_bar_layout.addWidget(self.instant_label)

        self.stability_label = QLabel("Stability: ---")
        self.stability_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #e65100; margin-right: 10px;")
        top_bar_layout.addWidget(self.stability_label)

        self.timer_label = QLabel("Time: 00:00:00")
        self.timer_label.setStyleSheet("font-size: 11pt; font-weight: bold; color: #333333;")
        top_bar_layout.addWidget(self.timer_label)

        main_layout.addLayout(top_bar_layout)

        # --- MATPLOTLIB PLOT WIDGET ---
        self.fig = Figure(figsize=(8, 5.5), facecolor='white')
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.ax = self.fig.add_subplot(111)
        
        self.ax.set_xlabel("Sample Index", fontsize=10, color='black')
        self.ax.set_ylabel("Current (Amps)", fontsize=10, color='black')
        self.ax.set_ylim(-50, 50)
        
        self.display_window = SAMPLES
        self.ax.set_xlim(0, self.display_window)
        self.ax.grid(True, linestyle='--', alpha=0.5)
        
        self.line, = self.ax.plot(np.arange(self.display_window), self.engine.current_data[:self.display_window], color='#1e90ff', linewidth=1.5)
        self.fig.tight_layout()
        
        main_layout.addWidget(self.canvas)

        # --- TIMER TO UPDATE UI ---
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_ui)
        self.timer.start(1000)

    def show_custom_message(self, title, text, icon=QMessageBox.Icon.Information):
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setIcon(icon)
        msg.setStyleSheet("""
            QMessageBox { background-color: #ffffff; }
            QLabel { color: #000000; font-size: 11pt; font-family: 'Segoe UI', Arial, sans-serif; }
            QPushButton { background-color: #e0e0e0; color: #000000; padding: 6px 12px; border-radius: 4px; min-width: 80px; font-size: 10pt; }
        """)
        msg.exec()

    def start_logging(self):
        self.session_counter += 1
        success, message, updated_counter = self.engine.start_logging(self.session_counter)
        self.session_counter = updated_counter

        if not success:
            self.show_custom_message("Folder Error", message, QMessageBox.Icon.Critical)
            return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def stop_logging(self):
        saved_folder = self.engine.stop_logging()
        
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        # Reset graph flat line instantly in GUI
        self.line.set_ydata(self.engine.current_data[:self.display_window])
        self.canvas.draw_idle()
        
        self.instant_label.setText("RMS: 0.0000 A  |  Peak: 0.0000 A")
        self.timer_label.setText("Time: 00:00:00")
        
        self.show_custom_message("Success", f"Batch Stopped.\n\nPer-minute CSV files saved inside:\n{saved_folder}")

    def update_ui(self):
        # Fetching properties directly from the engine backend
        if self.engine.is_logging:
            self.line.set_ydata(self.engine.current_data[:self.display_window])
            self.canvas.draw_idle()
            self.instant_label.setText(f"RMS: {self.engine.current_rms:7.4f} A  |  Peak: {self.engine.current_peak:7.4f} A")

        if self.engine.signal_status_text == "Active Load":
            self.connection_status_label.setText("Status: Active Load")
            self.connection_status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #2e7d32; margin-right: 10px;")
        else:
            self.connection_status_label.setText("Status: Disconnected / Noise")
            self.connection_status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #d32f2f; margin-right: 10px;")

        if self.engine.stability_text == "Stable":
            self.stability_label.setText("Stability: Stable")
            self.stability_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #2e7d32; margin-right: 10px;")
        elif self.engine.stability_text == "Fluctuating":
            self.stability_label.setText("Stability: Fluctuating")
            self.stability_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #f57c00; margin-right: 10px;")
        else:
            self.stability_label.setText("Stability: Analyzing...")
            self.stability_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #e65100; margin-right: 10px;")

        if self.engine.is_logging:
            if self.engine.start_time:
                elapsed = int(time.time() - self.engine.start_time)
                time_formatted = str(datetime.timedelta(seconds=elapsed))
                self.timer_label.setText(f"Time: {time_formatted}")
        else:
            self.timer_label.setText("Time: 00:00:00")

    def closeEvent(self, event):
        # Shut down backend safely when window closes
        self.engine.close()
        event.accept()

if __name__ == "__main__":
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        
    window = CNCApp()
    window.show()
    sys.exit(app.exec())