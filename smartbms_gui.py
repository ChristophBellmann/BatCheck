import tkinter as tk
from tkinter import ttk
import asyncio
import datetime
import csv
import os
import threading
from bleak import BleakClient

# Geräteadressen
devices = {
    "akku-1": "A4:C1:38:A0:D1:5B",
    "akku-2": "A4:C1:38:A0:A0:59"
}

CHAR_NOTIFY = "0000ff01-0000-1000-8000-00805f9b34fb"
CHAR_WRITE  = "0000ff02-0000-1000-8000-00805f9b34fb"
CMD_CELLS   = bytes.fromhex("DD A5 04 00 FF FC 77")
CMD_STATUS  = bytes.fromhex("DD A5 03 00 FF FD 77")

log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
csv_paths = {name: os.path.join(log_dir, f"{name}.csv") for name in devices}

# Pufferspeicher pro Gerät
notify_buffer = {name: bytearray() for name in devices}

# Aktuelle Daten pro Gerät
device_data = {name: {
    "voltages": [0.0]*16,
    "total": 0.0,
    "strom": 0.0,
    "soc": 0,
    "status": "",
    "connected": False,
    "last_update": ""
} for name in devices}

# Thread-Stopp-Event
stop_event = threading.Event()

# --------- BLE-Funktionen ---------
def debug_bytes(data):
    return " ".join(f"{b:02X}" for b in data)

def parse_cell_voltages(packet):
    if not packet.startswith(b'\xDD') or packet[1] != 0x04 or packet[-1] != 0x77:
        return []
    data = packet[4:-3]  # Header + CRC + Endbyte entfernen
    voltages = []
    for i in range(0, len(data), 2):
        if i+1 >= len(data): break
        v = int.from_bytes(data[i:i+2], 'big') / 1000.0
        voltages.append(v)
    return voltages

def parse_status(packet):
    # https://github.com/simat/JK-BMS/blob/master/docs/JBD%20Protocol.md
    if not packet.startswith(b'\xDD') or packet[1] != 0x03 or packet[-1] != 0x77:
        return None
    data = packet[4:-3]
    if len(data) < 22:
        return None
    # Gesamtspannung [V]
    voltage = int.from_bytes(data[0:2], "big") / 100.0
    # Strom [A] (signed!)
    raw_current = int.from_bytes(data[2:4], "big", signed=True)
    current = raw_current / 100.0
    # Restkapazität [Ah]
    rest_ah = int.from_bytes(data[4:6], "big") / 100.0
    # Nennkapazität [Ah]
    nominal_ah = int.from_bytes(data[6:8], "big") / 100.0
    # Zykluszahl
    cycles = int.from_bytes(data[8:10], "big")
    # SoC [%]
    soc = data[19]
    return {
        "Spannung": voltage,
        "Strom": current,
        "RestAh": rest_ah,
        "NennAh": nominal_ah,
        "Zyklen": cycles,
        "SoC": soc
    }

def handle_notify(name, data):
    buf = notify_buffer[name]
    buf += data
    # Suche nach vollständigen Paketen
    while True:
        if len(buf) < 7: break
        try:
            start = buf.index(0xDD)
            end = buf.index(0x77, start)
            packet = buf[start:end+1]
            notify_buffer[name] = buf[end+1:]
        except ValueError:
            break
        # Debug in Konsole:
        print(f"[{name}] [RAW] {packet.hex()}")
        if len(packet) > 5 and packet[1] == 0x04:
            voltages = parse_cell_voltages(packet)
            if voltages:
                device_data[name]["voltages"] = voltages
                device_data[name]["total"] = sum(voltages)
                now = datetime.datetime.now().strftime("%H:%M:%S")
                device_data[name]["last_update"] = now
                # Logging
                with open(csv_paths[name], "a", newline="") as f:
                    csv.writer(f).writerow([now] + voltages)
        elif len(packet) > 5 and packet[1] == 0x03:
            status = parse_status(packet)
            if status:
                device_data[name]["soc"] = status["SoC"]
                device_data[name]["strom"] = status["Strom"]
                # Statuszeile für GUI
                device_data[name]["status"] = (
                    f"⚡ {status['Spannung']:.2f} V, {status['Strom']:.2f} A, {status['RestAh']:.2f} Ah"
                    f", Nenn: {status['NennAh']:.2f} Ah, Zyklen: {status['Zyklen']}, SoC: {status['SoC']}%"
                )

async def monitor_bms(name, address):
    print(f"[{name}] 🔌 Verbinde mit {address}...")
    try:
        async with BleakClient(address) as client:
            print(f"[{name}] ✅ Verbunden")
            notify_buffer[name] = bytearray()
            device_data[name]["connected"] = True
            await client.start_notify(CHAR_NOTIFY, lambda _, d: handle_notify(name, d))
            await asyncio.sleep(1)
            while not stop_event.is_set():
                await client.write_gatt_char(CHAR_WRITE, CMD_CELLS)
                await asyncio.sleep(1.1)
                await client.write_gatt_char(CHAR_WRITE, CMD_STATUS)
                await asyncio.sleep(3.4)
    except Exception as e:
        print(f"[{name}] BLE-Fehler: {e}")
        device_data[name]["connected"] = False

# --------- GUI ---------
class BMSGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SmartBMS Dual Monitor")
        self.configure(bg="#232934")
        self.bms_frames = {}
        self._build_gui()
        self.protocol("WM_DELETE_WINDOW", self.stop)
        self.after(500, self.update_gui)

    def _build_gui(self):
        self.header = tk.Label(self, text="SmartBMS Monitor", font=("Segoe UI", 20, "bold"),
                               bg="#232934", fg="#1abc9c")
        self.header.pack(pady=(8, 2))
        main_frame = tk.Frame(self, bg="#232934")
        main_frame.pack(padx=18, pady=10, fill="both")
        for col, name in enumerate(devices):
            frame = tk.Frame(main_frame, bg="#232934", bd=1, relief="ridge")
            frame.grid(row=0, column=col, padx=16, sticky="n")
            title = tk.Label(frame, text=f"{name}", font=("Segoe UI", 14, "bold"),
                             bg="#232934", fg="#eee")
            title.pack(pady=(4, 4))
            self.bms_frames[name] = {}
            # Gesamtspannung
            self.bms_frames[name]["vlabel"] = tk.Label(frame, text="Gesamt: -- V", font=("Segoe UI", 16, "bold"),
                                                       bg="#232934", fg="#1abc9c")
            self.bms_frames[name]["vlabel"].pack(pady=(5, 0))
            # Strom fett
            self.bms_frames[name]["ilabel"] = tk.Label(frame, text="Strom: -- A", font=("Segoe UI", 15, "bold"),
                                                       bg="#232934", fg="#ee7722")
            self.bms_frames[name]["ilabel"].pack(pady=(0, 5))
            # SoC
            self.bms_frames[name]["soc"] = tk.Label(frame, text="SoC: -- %", font=("Segoe UI", 13),
                                                    bg="#232934", fg="#f5ba41")
            self.bms_frames[name]["soc"].pack(pady=2)
            # Zellbalken
            cell_frame = tk.Frame(frame, bg="#232934")
            cell_frame.pack(pady=8)
            self.bms_frames[name]["bars"] = []
            for i in range(16):
                bar_bg = "#333842" if i % 2 == 0 else "#2a2d35"
                f = tk.Frame(cell_frame, bg=bar_bg)
                f.pack(fill="x", padx=2, pady=1)
                b = ttk.Progressbar(f, length=160, mode="determinate")
                b.pack(side="left", padx=(4, 7), pady=0)
                l = tk.Label(f, text="--.- V", width=7, anchor="w", font=("Consolas", 12, "bold"),
                             bg=bar_bg, fg="#7fffd4")
                l.pack(side="left")
                self.bms_frames[name]["bars"].append((b, l))
            # Statusinfo
            self.bms_frames[name]["status"] = tk.Label(frame, text="--", font=("Consolas", 11),
                                                       bg="#232934", fg="#BBB")
            self.bms_frames[name]["status"].pack(pady=(5, 1))
            # Verbunden-Anzeige
            self.bms_frames[name]["conn"] = tk.Label(frame, text="⏳ Warte...", font=("Segoe UI", 11, "italic"),
                                                     bg="#232934", fg="#999")
            self.bms_frames[name]["conn"].pack(pady=(2, 2))

        # Buttonleiste
        btn_frame = tk.Frame(self, bg="#232934")
        btn_frame.pack(pady=(8, 6))
        self.stop_btn = tk.Button(btn_frame, text="❌ Stop/Exit", font=("Segoe UI", 13, "bold"),
                                  command=self.stop, bg="#ce2e2e", fg="#fff", bd=0, padx=28, pady=6)
        self.stop_btn.pack(side="left", padx=12)
        self.log_btn = tk.Button(btn_frame, text="⏺️ Logging", font=("Segoe UI", 13, "bold"),
                                 command=self.show_log_dir, bg="#244b89", fg="#fff", bd=0, padx=18, pady=6)
        self.log_btn.pack(side="left", padx=12)

    def update_gui(self):
        for name in devices:
            frame = self.bms_frames[name]
            d = device_data[name]
            # Verbunden-Anzeige
            if d["connected"]:
                frame["conn"].config(text=f"✓ Verbunden ({d['last_update']})", fg="#40ec83")
            else:
                frame["conn"].config(text="✗ Nicht verbunden", fg="#ce2e2e")
            # Gesamtspannung und Strom
            if d["total"] > 0:
                frame["vlabel"].config(text=f"Gesamt: {d['total']:.3f} V")
            else:
                frame["vlabel"].config(text="Gesamt: -- V")
            frame["ilabel"].config(text=f"Strom: {d['strom']:.2f} A")
            frame["soc"].config(text=f"SoC: {d['soc']} %")
            frame["status"].config(text=d["status"])
            # Zellspannungen als Balken
            for i, (bar, lab) in enumerate(frame["bars"]):
                try:
                    v = d["voltages"][i]
                    bar["value"] = v * 100  # Balkenlänge, bei z.B. 4.2 V = 420
                    bar["maximum"] = 4.3 * 100
                    lab["text"] = f"{v:.3f} V"
                    lab["fg"] = "#7fffd4" if 3.1 < v < 4.25 else "#fd7b7b"
                except IndexError:
                    bar["value"] = 0
                    lab["text"] = "--.- V"
                    lab["fg"] = "#999"
        self.after(500, self.update_gui)

    def show_log_dir(self):
        os.system(f"xdg-open '{log_dir}'")

    def stop(self):
        stop_event.set()
        self.destroy()
        print("⛔️ Beende Programm...")

def setup_styles():
    style = ttk.Style()
    style.theme_use("default")
    style.configure("TProgressbar", thickness=13, troughcolor="#232934",
                    background="#40ec83", bordercolor="#232934", lightcolor="#34dabb", darkcolor="#1abc9c")

# ----- Async-Thread-Runner -----
def start_async_loop():
    asyncio.run(run_all_monitors())

async def run_all_monitors():
    tasks = []
    for name, addr in devices.items():
        tasks.append(asyncio.create_task(monitor_bms(name, addr)))
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    # BLE-Loop im Thread
    threading.Thread(target=start_async_loop, daemon=True).start()
    app = BMSGUI()
    setup_styles()
    app.mainloop()
