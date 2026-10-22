# ⚡ SysCast Telemetry

![Version](https://img.shields.io/badge/Version-1.0.0--Beta-b45cff?style=for-the-badge)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows)
![License](https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge)

**SysCast** is a lightweight PC hardware monitor that streams data from MSI Afterburner directly to your phone's browser. No apps, no accounts, no cables.

## ✨ Features
* 📱 **Mobile Dashboard:** Track FPS, CPU, GPU, and RAM on any phone.
* 🚫 **NO OSD NEEDED:** Reads directly from memory. You don't need Afterburner's On-Screen Display enabled on your PC!
* 🔋 **Always Awake Button:** A dedicated toggle in the dashboard to keep your phone screen on forever while gaming.
* ⚡ **Real-Time:** 20Hz update rate for zero-delay tracking.
* 👻 **Hidden Mode:** Press `H` in the console to minimize it to the System Tray.

---

## 🎮 Quick Start (For Gamers)

1. **Setup MSI Afterburner:** 
   Open Settings ➔ Monitoring tab. Check the tick-box next to any graph you want to stream (e.g., Framerate). Click Apply. 
2. **Run SysCast:** 
   Download `syscast.exe` from the [Releases](https://github.com/HusseinWalid48/SysCast/releases) page and run it.
3. **Connect:** 
   Scan the **QR Code** with your phone (both devices must be on the same Wi-Fi).

---

## ⌨️ Server Controls
* `H` : Hide to System Tray (runs silently).
* `C` : Copy the link to clipboard.
* `O` : Open on your PC's browser.
* `⌫ Backspace` : Stop the server.

---

## 🛠️ For Developers (Build from Source)

```bash
# 1. Clone & enter directory
git clone [https://github.com/HusseinWalid48/SysCast.git](https://github.com/HusseinWalid48/SysCast.git)
cd SysCast

# 2. Install dependencies
pip install fastapi uvicorn websockets psutil qrcode pillow pystray

# 3. Run directly
python syscast.py

# 4. Compile to .exe (Optional)
python -m pyinstaller --onefile syscast.py
```

---
**License:** This project is licensed under the MIT License.
