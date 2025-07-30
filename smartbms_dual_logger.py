import asyncio
import datetime
import csv
import os
from bleak import BleakClient

# Geräteadressen (Name: MAC)
devices = {
    "akku-1": "A4:C1:38:A0:D1:5B",
    "akku-2": "A4:C1:38:A0:A0:59"
}

# BLE-UUIDs und Kommandos
CHAR_NOTIFY = "0000ff01-0000-1000-8000-00805f9b34fb"
CHAR_WRITE  = "0000ff02-0000-1000-8000-00805f9b34fb"
CMD_CELLS   = bytes.fromhex("DD A5 04 00 FF FC 77")
CMD_STATUS  = bytes.fromhex("DD A5 03 00 FF FD 77")

log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

# Buffer und CSV pro Gerät
notify_buffer = {name: bytearray() for name in devices}
csv_paths = {name: os.path.join(log_dir, f"{name}.csv") for name in devices}

def debug_bytes(data):
    return " ".join(f"{b:02X}" for b in data)

def parse_cell_voltages(packet):
    # Prüfe auf korrektes Paket: DD 04 ... 77
    if not packet.startswith(b'\xDD') or packet[1] != 0x04 or packet[-1] != 0x77:
        print("❌ Kein Zellspannungs-Paket!")
        return []
    data = packet[4:-3]  # Header (4 Byte) und Footer (3 Byte) entfernen
    voltages = []
    for i in range(0, len(data), 2):
        if i + 1 >= len(data):
            break
        v = int.from_bytes(data[i:i+2], 'big') / 1000.0
        voltages.append(v)
    return voltages

def parse_status(packet):
    # Prüfe auf korrektes Paket: DD 03 ... 77
    if not packet.startswith(b'\xDD') or packet[1] != 0x03 or packet[-1] != 0x77:
        return None
    data = packet[4:-3]
    if len(data) < 22:
        print("⚠️ Statusdaten zu kurz")
        return None
    total_voltage = int.from_bytes(data[0:2], 'big') / 100.0
    current = int.from_bytes(data[2:4], 'big', signed=True) / 100.0
    residual_capacity = int.from_bytes(data[4:6], 'big') / 100.0
    nominal_capacity = int.from_bytes(data[6:8], 'big') / 100.0
    cycles = int.from_bytes(data[8:10], 'big')
    soc = data[21]
    return {
        "Spannung": total_voltage,
        "Strom": current,
        "RestAh": residual_capacity,
        "NennAh": nominal_capacity,
        "Zyklen": cycles,
        "SoC": soc,
    }

async def monitor_bms(name, address):
    print(f"[{name}] 🔌 Verbinde mit {address}...")
    try:
        async with BleakClient(address) as client:
            print(f"[{name}] ✅ Verbunden")
            notify_buffer[name] = bytearray()
            await client.start_notify(CHAR_NOTIFY, lambda _, d: handle_notify(name, d))
            await asyncio.sleep(1)
            await client.write_gatt_char(CHAR_WRITE, CMD_CELLS)
            await asyncio.sleep(0.2)
            await client.write_gatt_char(CHAR_WRITE, CMD_STATUS)
            while True:
                await asyncio.sleep(5)
                await client.write_gatt_char(CHAR_WRITE, CMD_CELLS)
                await asyncio.sleep(0.2)
                await client.write_gatt_char(CHAR_WRITE, CMD_STATUS)
    except Exception as e:
        print(f"[{name}] BLE-Fehler: {e}")

def handle_notify(name, data):
    notify_buffer[name] += data
    # Wir suchen nach Paketen, die mit DD starten und mit 77 enden
    while True:
        buf = notify_buffer[name]
        if len(buf) < 2:
            break
        try:
            start = buf.index(0xDD)
            end = buf.index(0x77, start)
            packet = buf[start:end+1]
            notify_buffer[name] = buf[end+1:]
        except ValueError:
            break

        print(f"[{name}] [RAW] {packet.hex()}")
        if packet[1] == 0x04:
            voltages = parse_cell_voltages(packet)
            if voltages:
                now = datetime.datetime.now().strftime("%H:%M:%S")
                total_v = sum(voltages)
                print(f"[{name}] 🔋 Zellspannungen: " + " | ".join(f"{v:.3f} V" for v in voltages))
                print(f"[{name}] ➡️ Gesamtspannung (Summe Zellen): {total_v:.3f} V")
                with open(csv_paths[name], "a", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow([now] + voltages)
        elif packet[1] == 0x03:
            status = parse_status(packet)
            if status:
                print(f"[{name}] ⚡️ Spannung: {status['Spannung']:.2f} V | Strom: {status['Strom']:.2f} A | Rest: {status['RestAh']:.2f} Ah | Nenn: {status['NennAh']:.2f} Ah | Zyklen: {status['Zyklen']} | SoC: {status['SoC']}%")

async def main():
    tasks = []
    for name, address in devices.items():
        tasks.append(asyncio.create_task(monitor_bms(name, address)))
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("⛔️ Beende Programm...")
