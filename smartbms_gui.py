import tkinter as tk
from tkinter import ttk
import asyncio
import threading
import datetime
import csv
import os
from bleak import BleakClient

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

notify_buffer = {name: bytearray() for name in devices}
device_data = {
    name: dict(
        connected=False,
        status="Warte...",
        last_update="--:--:--",
        total=0.0,
        strom=0.0,
        soc=0,
        voltages=[0.0]*16,
    ) for name in devices
}
stop_event = threading.Event()
log_active = threading.Event()

def parse_cell_voltages(packet):
    if not packet.startswith(b'\xDD') or packet[1] != 0x04 or packet[-1] != 0x77:
        return []
    data = packet[4:-3]
    voltages = []
    for i in range(0, len(data), 2):
        if i + 1 >= len(data): break
        v = int.from_bytes(data[i:i+2], 'big') / 1000.0
        voltages.append(v)
    return voltages

def parse_status(packet):
    if not packet.startswith(b'\xDD') or packet[1] != 0x03 or packet[-1] != 0x77:
        return {}
    d = packet
    total_v = int.from_bytes(d[4:6], "big") / 100.0
    strom_raw = int.from_bytes(d[6:8], "big", signed=True)
    strom = strom_raw / 100.0
    soc = d[23] if len(d) > 23 else 0
    return dict(total=total_v, strom=strom, soc=soc)

async def monitor_bms(name, address):
    while not stop_event.is_set():
        try:
            device_data[name].update(connected=False, status="Scanne...")
            async with BleakClient(address) as client:
                device_data[name].update(connected=True, status="Verbunden")
                notify_buffer[name] = bytearray()
                await client.start_notify(CHAR_NOTIFY, lambda _, d: handle_notify(name, d))
                while not stop_event.is_set():
                    await client.write_gatt_char(CHAR_WRITE, CMD_CELLS)
                    await asyncio.sleep(0.7)
                    await client.write_gatt_char(CHAR_WRITE, CMD_STATUS)
                    await asyncio.sleep(4.3)
        except Exception as e:
            device_data[name].update(connected=False, status=f"Fehler: {str(e)[:25]}")
        await asyncio.sleep(3)

def handle_notify(name, data):
    buf = notify_buffer[name]
    buf += data
    while True:
        if len(buf) < 7: break
        try:
            start = buf.index(0xDD)
            end = buf.index(0x77, start)
        except ValueError: break
        packet = buf[start:end+1]
        buf = buf[end+1:]
        print(f"[{name}] [RAW] {packet.hex()}")
        if len(packet) > 4 and packet[1] == 0x04:
            voltages = parse_cell_voltages(packet)
            device_data[name]["voltages"] = voltages + [0.0]*(16-len(voltages))
            now = datetime.datetime.now().strftime("%H:%M:%S")
            device_data[name]["last_update"] = now
            # **Gesamtspannung = Summe Zellspannungen**
            if voltages:
                device_data[name]["total"] = sum(voltages)
            if log_active.is_set() and voltages:
                with open(csv_paths[name], "a", newline="") as f:
                    csv.writer(f).writerow([now]+voltages)
        elif len(packet) > 4 and packet[1] == 0x03:
            s = parse_status(packet)
            if s:
                # Strom & SoC (Gesamtspannung nehmen wir NUR als fallback)
                device_data[name]["strom"] = s["strom"]
                device_data[name]["soc"] = s["soc"]
                if not any(device_data[name]["voltages"]):
                    device_data[name]["total"] = s["total"]
        notify_buffer[name] = buf

class BMSGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SmartBMS Dual Monitor")
        self.configure(bg="#222834")
        self.bms_frames = {}
        self._build_gui()
        self.protocol("WM_DELETE_WINDOW", self.stop)
        self.after(500, self.update_gui)

    def _build_gui(self):
        self.header = tk.Label(self, text="SmartBMS Monitor", font=("Segoe UI", 22, "bold"),
                               bg="#222834", fg="#19e2ba")
        self.header.pack(pady=(8, 2))
        main_frame = tk.Frame(self, bg="#222834")
        main_frame.pack(padx=12, pady=10, fill="both")
        for col, name in enumerate(devices):
            frame = tk.Frame(main_frame, bg="#232b36", bd=1, relief="ridge")
            frame.grid(row=0, column=col, padx=18, sticky="n")
            title = tk.Label(frame, text=f"{name}", font=("Segoe UI", 15, "bold"),
                             bg="#232b36", fg="#eee")
            title.pack(pady=(8, 2))
            addr_lbl = tk.Label(frame, text=f"{devices[name]}", font=("Consolas", 9), 
                               bg="#232b36", fg="#67a", anchor="w", width=24)
            addr_lbl.pack(pady=(0,1))
            self.bms_frames[name] = {}
            self.bms_frames[name]["addr_lbl"] = addr_lbl
            self.bms_frames[name]["vlabel"] = tk.Label(frame, text="Gesamt: -- V", font=("Segoe UI", 16, "bold"),
                                                       bg="#232b36", fg="#16e2ba")
            self.bms_frames[name]["vlabel"].pack(pady=(5, 0))
            self.bms_frames[name]["ilabel"] = tk.Label(frame, text="Strom: -- A", font=("Segoe UI", 15, "bold"),
                                                       bg="#232b36", fg="#fe912a")
            self.bms_frames[name]["ilabel"].pack(pady=(0, 0))
            self.bms_frames[name]["soc"] = tk.Label(frame, text="SoC: -- %", font=("Segoe UI", 14, "bold"),
                                                    bg="#232b36", fg="#ffb140")
            self.bms_frames[name]["soc"].pack(pady=2)
            cell_frame = tk.Frame(frame, bg="#232b36")
            cell_frame.pack(pady=8)
            self.bms_frames[name]["bars"] = []
            for i in range(16):
                bar_bg = "#232b36" if i % 2 == 0 else "#273040"
                f = tk.Frame(cell_frame, bg=bar_bg)
                f.pack(fill="x", padx=2, pady=1)
                num = tk.Label(f, text=f"{i+1:02d}", width=3, font=("Consolas", 11, "bold"), bg=bar_bg, fg="#888")
                num.pack(side="left", padx=(0,3))
                b = ttk.Progressbar(f, length=145, mode="determinate")
                b.pack(side="left", padx=(1, 6), pady=0)
                l = tk.Label(f, text="--.- V", width=7, anchor="w", font=("Consolas", 12, "bold"),
                             bg=bar_bg, fg="#18fbd4")
                l.pack(side="left")
                self.bms_frames[name]["bars"].append((b, l, num))
            self.bms_frames[name]["conn"] = tk.Label(frame, text="⏳ Warte...", font=("Consolas", 11),
                                                     bg="#232b36", fg="#BBB", anchor="w", width=32)
            self.bms_frames[name]["conn"].pack(pady=(6, 4))

        btn_frame = tk.Frame(self, bg="#222834")
        btn_frame.pack(pady=(6, 10))
        self.stop_btn = tk.Button(btn_frame, text="✖ Stop/Exit", font=("Segoe UI", 13, "bold"),
                                  command=self.stop, bg="#de3a3a", fg="#fff", bd=0, padx=28, pady=8)
        self.stop_btn.pack(side="left", padx=16)
        self.log_btn = tk.Button(btn_frame, text="● Logging", font=("Segoe UI", 13, "bold"),
                                 command=self.toggle_logging, bg="#184b87", fg="#fff", bd=0, padx=18, pady=8)
        self.log_btn.pack(side="left", padx=16)
        self._logging = False

    def update_gui(self):
        for name in devices:
            frame = self.bms_frames[name]
            d = device_data[name]
            if d["connected"]:
                t = f"✓ Verbunden ({d['last_update']})"
            elif "Fehler" in d["status"]:
                t = d["status"][:30] + ("…" if len(d["status"]) > 30 else "")
            else:
                t = d["status"]
            frame["conn"].config(text=t)
            volt_sum = sum(d["voltages"])
            if volt_sum > 2:
                frame["vlabel"].config(text=f"Gesamt: {volt_sum:.3f} V")
            elif d["total"] > 0:
                frame["vlabel"].config(text=f"Gesamt: {d['total']:.3f} V")
            else:
                frame["vlabel"].config(text="Gesamt: -- V")
            frame["ilabel"].config(text=f"Strom: {d['strom']:.2f} A")
            frame["soc"].config(text=f"SoC: {d['soc']} %")
            for i, (bar, lab, num) in enumerate(frame["bars"]):
                try:
                    v = d["voltages"][i]
                    bar["value"] = v * 100
                    bar["maximum"] = 4.3 * 100
                    lab["text"] = f"{v:.3f} V"
                    lab["fg"] = "#18fbd4" if 3.1 < v < 4.25 else "#fd4b4b"
                except IndexError:
                    bar["value"] = 0
                    lab["text"] = "--.- V"
                    lab["fg"] = "#999"
        self.log_btn.config(bg="#0b75da" if self._logging else "#184b87")
        self.after(500, self.update_gui)

    def toggle_logging(self):
        self._logging = not self._logging
        if self._logging:
            log_active.set()
        else:
            log_active.clear()

    def stop(self):
        stop_event.set()
        self.destroy()
        print("⛔️ Beende Programm...")

def setup_styles(root):
    style = ttk.Style(root)
    style.theme_use("default")
    style.configure("TProgressbar", thickness=13, troughcolor="#232b36",
                    background="#18fbd4", bordercolor="#232934", lightcolor="#45e3ba", darkcolor="#19e2ba")

def run_asyncio_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    tasks = []
    for name, addr in devices.items():
        tasks.append(loop.create_task(monitor_bms(name, addr)))
    try:
        loop.run_until_complete(asyncio.gather(*tasks))
    except Exception as e:
        print("Asyncio-Thread: ", e)
    finally:
        loop.close()

if __name__ == "__main__":
    gui = BMSGUI()
    setup_styles(gui)
    t = threading.Thread(target=run_asyncio_thread, daemon=True)
    t.start()
    gui.mainloop()
    stop_event.set()
