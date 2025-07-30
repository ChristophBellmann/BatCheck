import asyncio
import sys
from bleak import BleakClient

async def main(address):
    async with BleakClient(address) as client:
        print(f"\n✅ Verbunden mit {address} – zeige GATT-Dienste:\n")
        for service in client.services:
            print(f"🔧 Service {service.uuid}")
            for char in service.characteristics:
                print(f"  📌 Char  {char.uuid} – {char.properties}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scan_services.py <BLE-ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
