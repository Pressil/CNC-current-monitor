import sys
import os
import threading
import time
import datetime
import csv
import queue
from collections import deque
import numpy as np
import nidaqmx
from nidaqmx.constants import AcquisitionType

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QMessageBox)
from PyQt6.QtCore import QTimer, Qt

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

# --- CONFIRMED HARDWARE CONFIGURATION ---
TRANSFORMATION_RATIO = 3000
BURDEN_RESISTANCE_OHMS = 1000.0  
DAQ_CHANNEL = "cDAQ1Mod1/ai7" 

SAMPLE_RATE = 20000
SAMPLES = 20000 
LOG_FOLDER = r"C:\Users\ADMIN\OneDrive\Desktop\current data"

# --- BACKGROUND CALIBRATION FACTOR ---
BACKGROUND_CALIBRATION_FACTOR = 0.9086

# --- NOISE GATE & STABILITY SETTINGS ---
# Set to 0.15 A so lower phase currents (like 0.4 A on phase W) pass through while filtering open-circuit noise
DISCONNECTION_NOISE_THRESHOLD = 0.15  
STABILITY_WINDOW_SIZE = 5  # Number of seconds to evaluate for stability
STABILITY_STD_DEV_LIMIT = 0.05  # Max standard deviation to be considered "Stable"

# --- HARDWARE RESET (Fixes Kernel Lockups) ---
try:
    device_name = DAQ_CHANNEL.split('/')[0]
    nidaqmx.system.Device(device_name).reset_device()
    print(f"Hardware Reset Complete: {device_name} is now free.")
except Exception as e:
    print(f"Note: Could not perform startup hardware reset. ({e})")

class CNCApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CNC Current Monitor - Per-Minute File Logger")
        self.resize(1000, 700)
        
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

        # Thread-safe data containers and state flags
        self.current_data = np.zeros(SAMPLES)
        self.raw_ac_voltage_batch = np.zeros(SAMPLES) 
        self.current_peak = 0.0  
        self.current_rms = 0.0  
        self.signal_status_text = "Disconnected / No Load"
        self.stability_text = "Analyzing..."
        
        self.rms_history = deque(maxlen=STABILITY_WINDOW_SIZE)

        self.calibration_factor = BACKGROUND_CALIBRATION_FACTOR
        self.total_samples = 0
        self.session_counter = 0 
        self.current_session_folder = ""
        
        self.data_queue = queue.Queue()
        self.app_running = True   
        self.is_logging = False   
        self.start_time = None    

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
        
        self.display_window = 20000
        self.ax.set_xlim(0, self.display_window)
        self.ax.grid(True, linestyle='--', alpha=0.5)
        
        self.line, = self.ax.plot(np.arange(self.display_window), self.current_data[:self.display_window], color='#1e90ff', linewidth=1.5)
        self.fig.tight_layout()
        
        main_layout.addWidget(self.canvas)

        # --- START BACKGROUND THREADS ---
        self.daq_thread = threading.Thread(target=self.acquire_data, daemon=True)
        self.daq_thread.start()
        
        self.csv_thread = threading.Thread(target=self.save_csv_data, daemon=True)
        self.csv_thread.start()

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
        folder_name = f"session_{self.session_counter:02d}"
        self.current_session_folder = os.path.join(LOG_FOLDER, folder_name)
        
        while os.path.exists(self.current_session_folder):
            self.session_counter += 1
            folder_name = f"session_{self.session_counter:02d}"
            self.current_session_folder = os.path.join(LOG_FOLDER, folder_name)

        try:
            os.makedirs(self.current_session_folder)
        except Exception as e:
            self.show_custom_message("Folder Error", f"Cannot create directory:\n{e}", QMessageBox.Icon.Critical)
            return

        self.total_samples = 0
        with self.data_queue.mutex:
            self.data_queue.queue.clear()

        self.rms_history.clear()
        self.start_time = time.time()
        self.is_logging = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def stop_logging(self):
        self.is_logging = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        # --- RESET GRAPH & READOUTS TO FLAT LINE ON STOP ---
        self.current_data = np.zeros(SAMPLES)
        self.current_rms = 0.0
        self.current_peak = 0.0
        self.signal_status_text = "Disconnected / No Load"
        self.stability_text = "Analyzing..."
        self.rms_history.clear()
        
        self.line.set_ydata(self.current_data[:self.display_window])
        self.canvas.draw_idle()
        
        self.instant_label.setText("RMS: 0.0000 A  |  Peak: 0.0000 A")
        self.timer_label.setText("Time: 00:00:00")
        
        self.show_custom_message("Success", f"Batch Stopped.\n\nPer-minute CSV files saved inside:\n{self.current_session_folder}")

    def acquire_data(self):
        while self.app_running:
            try:
                with nidaqmx.Task() as task:
                    task.ai_channels.add_ai_voltage_chan(DAQ_CHANNEL, min_val=-60.0, max_val=60.0) 
                    task.timing.cfg_samp_clk_timing(
                        rate=SAMPLE_RATE, 
                        samps_per_chan=SAMPLES,
                        sample_mode=AcquisitionType.CONTINUOUS
                    )
                    task.start()

                    while self.app_running:
                        try:
                            raw_voltage = np.array(task.read(number_of_samples_per_channel=SAMPLES, timeout=2.0))
                            
                            ac_voltage = raw_voltage - np.mean(raw_voltage)
                            self.raw_ac_voltage_batch = ac_voltage 
                            
                            secondary_current = ac_voltage / BURDEN_RESISTANCE_OHMS
                            raw_primary_current = secondary_current * TRANSFORMATION_RATIO
                            
                            calculated_rms = np.sqrt(np.mean(raw_primary_current**2))

                            # --- NOISE GATE / DISCONNECTION CHECK ---
                            if calculated_rms < DISCONNECTION_NOISE_THRESHOLD:
                                primary_current = np.zeros_like(raw_primary_current)
                                self.current_rms = 0.0
                                self.current_peak = 0.0
                                self.signal_status_text = "Disconnected / No Load"
                            else:
                                primary_current = raw_primary_current * self.calibration_factor
                                self.current_rms = np.sqrt(np.mean(primary_current**2))
                                self.current_peak = np.max(np.abs(primary_current))
                                self.signal_status_text = "Active Load"

                            # Push to rolling stability window
                            self.rms_history.append(self.current_rms)
                            if len(self.rms_history) >= STABILITY_WINDOW_SIZE:
                                std_dev = np.std(list(self.rms_history))
                                if std_dev < STABILITY_STD_DEV_LIMIT:
                                    self.stability_text = "Stable"
                                else:
                                    self.stability_text = "Fluctuating"
                            else:
                                self.stability_text = "Analyzing..."

                            self.total_samples += SAMPLES
                            elapsed_sec = int(self.total_samples / SAMPLE_RATE)
                            
                            # --- CALCULATE REAL-TIME CLOCK FOR BATCH ---
                            batch_end_time = time.time()
                            batch_start_time = batch_end_time - (SAMPLES / SAMPLE_RATE)
                            times = batch_start_time + np.arange(SAMPLES) / SAMPLE_RATE
                            
                            self.current_data = primary_current

                            if self.is_logging:
                                # Send arrays to the background thread for fast processing
                                self.data_queue.put((elapsed_sec, times, primary_current))
                                
                        except nidaqmx.errors.DaqError as de:
                            print(f"DAQ Read Error: {de}")
                            break

            except Exception as e:
                print(f"DAQ Connection Error/Waiting: {e}")
                time.sleep(1.0)

    def save_csv_data(self):
        """Continuously formats and appends incoming data into per-minute CSV files."""
        while self.app_running:
            try:
                elapsed_sec, times, primary_current = self.data_queue.get(timeout=0.5)
                
                if self.is_logging and self.current_session_folder:
                    minute_idx = elapsed_sec // 60
                    file_name = f"minute_{minute_idx:02d}.csv"
                    file_path = os.path.join(self.current_session_folder, file_name)
                    
                    file_exists = os.path.exists(file_path)
                    
                    # Convert Epoch float times to human-readable strings (HH:MM:SS.fffff)
                    batch_rows = [
                        [datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S.%f'), f"{curr:.4f}"] 
                        for t, curr in zip(times, primary_current)
                    ]
                    
                    with open(file_path, mode='a', newline='') as file:
                        writer = csv.writer(file)
                        if not file_exists:
                            writer.writerow(["Time (HH:MM:SS)", "Current (A)"])
                        writer.writerows(batch_rows)  
                        file.flush()
                        os.fsync(file.fileno())
                        
                self.data_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"CSV Writer Error: {e}")

    def update_ui(self):
        if self.is_logging:
            self.line.set_ydata(self.current_data[:self.display_window])
            self.canvas.draw_idle()
            self.instant_label.setText(f"RMS: {self.current_rms:7.4f} A  |  Peak: {self.current_peak:7.4f} A")

        if self.signal_status_text == "Active Load":
            self.connection_status_label.setText("Status: Active Load")
            self.connection_status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #2e7d32; margin-right: 10px;")
        else:
            self.connection_status_label.setText("Status: Disconnected / Noise")
            self.connection_status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #d32f2f; margin-right: 10px;")

        if self.stability_text == "Stable":
            self.stability_label.setText("Stability: Stable")
            self.stability_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #2e7d32; margin-right: 10px;")
        elif self.stability_text == "Fluctuating":
            self.stability_label.setText("Stability: Fluctuating")
            self.stability_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #f57c00; margin-right: 10px;")
        else:
            self.stability_label.setText("Stability: Analyzing...")
            self.stability_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #e65100; margin-right: 10px;")

        if self.is_logging:
            if self.start_time:
                elapsed = int(time.time() - self.start_time)
                time_formatted = str(datetime.timedelta(seconds=elapsed))
                self.timer_label.setText(f"Time: {time_formatted}")
        else:
            self.timer_label.setText("Time: 00:00:00")

    def closeEvent(self, event):
        self.app_running = False
        if self.daq_thread.is_alive():
            self.daq_thread.join(timeout=2.0)
        if self.csv_thread.is_alive():
            self.csv_thread.join(timeout=2.0)
        event.accept()

if __name__ == "__main__":
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        
    window = CNCApp()
    window.show()
    sys.exit(app.exec())