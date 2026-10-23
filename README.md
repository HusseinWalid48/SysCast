<div align="center">

<pre>
███████╗██╗   ██╗███████╗ ██████╗ █████╗ ███████╗████████╗
██╔════╝╚██╗ ██╔╝██╔════╝██╔════╝██╔══██╗██╔════╝╚══██╔══╝
███████╗ ╚████╔╝ ███████╗██║     ███████║███████╗   ██║   
╚════██║  ╚██╔╝  ╚════██║██║     ██╔══██║╚════██║   ██║   
███████║   ██║   ███████║╚██████╗██║  ██║███████║   ██║   
╚══════╝   ╚═╝   ╚══════╝ ╚═════╝╚═╝  ╚═╝╚══════╝   ╚═╝   
</pre>
**Ultra-low latency PC hardware monitor streamed directly to your mobile browser.**

[![Latest Release](https://img.shields.io/badge/Download-v1.0.0--Beta-b45cff?style=for-the-badge&logo=github)](https://github.com/HusseinWalid48/SysCast/releases/latest)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows)](#)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](#)
[![License](https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge)](#)
<div align="center">
  <img src="https://github.com/user-attachments/assets/697bae74-5ae8-4a1b-8719-fc51bd7eacc9" width="49%" />
  <img src="https://github.com/user-attachments/assets/9163c73b-04ec-4b8b-9351-6bfad6cd94f2" width="49%" />
</div>

*No mobile apps. No user accounts. No cables. Just scan and play.*

</div>

---

> [!TIP]
> **🚫 NO OSD NEEDED!** SysCast reads performance data directly from MSI Afterburner's shared memory. You do **not** need Rivatuner (RTSS) or any On-Screen Display cluttering your game screen.

## 🔥 Key Features

* 📱 **Instant Mobile Dashboard:** Turn any phone or tablet into a dedicated hardware screen for FPS, Frametime, CPU, GPU, and RAM.
* 🔆 **Interactive "Always Awake" Toggle:** Built-in Wake Lock button right on the mobile screen to prevent your phone from locking mid-game.
* ⚡ **20Hz Ultra-Fast Polling:** Streams telemetry 20 times per second via WebSockets for zero-lag monitoring.
* 🛡️ **VPN-Proof Network Detection:** Automatically detects your true local Wi-Fi IP address, bypassing virtual VPN adapters.
* 👻 **Stealth Tray Mode:** Hide the console window to your Windows System Tray with a single keypress.

---

## 🚀 Quick Start (For Gamers)

1. **Configure MSI Afterburner:**
   Open **MSI Afterburner** ➔ **Settings** ⚙️ ➔ **Monitoring** tab. Check the tick-box `✔️` next to the stats you want to track (e.g., *Framerate, GPU Temp, CPU Usage*) and click **Apply**.
2. **Launch SysCast:**
   Download **`syscast.exe`** from the [**Releases Page**](https://github.com/HusseinWalid48/SysCast/releases) and double-click to run it.
3. **Scan & Connect:**
   Point your phone's camera at the **QR Code** in the console (ensure both your PC and phone are on the same Wi-Fi network).

---

## ⌨️ Interactive Server Controls

While the SysCast console window is active, use these physical keyboard shortcuts:

| Shortcut Key | Action | Description |
| :---: | :--- | :--- |
| <kbd>H</kbd> | **Hide to Tray** | Minimizes the terminal completely to the Windows System Tray (runs silently). |
| <kbd>C</kbd> | **Copy URL** | Copies the local dashboard link directly to your clipboard. |
| <kbd>O</kbd> | **Open Local** | Launches the dashboard in your default PC web browser for quick testing. |
| <kbd>⌫ Backspace</kbd> | **Stop Server** | Safely shuts down the telemetry stream and exits the active session. |

---

## 🧩 Advanced & Developer Options

<details>
<summary><b>📡 Telemetry Data Sources (Click to Expand)</b></summary>
<br>

SysCast extracts hardware metrics directly from memory with absolutely zero overhead. Here is exactly where your dashboard data comes from:

| Metric | Primary Source | Fallback / Note |
| :--- | :--- | :--- |
| **FPS & Frametime** | `RTSS` (RivaTuner) | MSI Afterburner (MAHM) |
| **CPU** *(Usage, Temp, Power)* | `MSI Afterburner` | Direct from Shared Memory |
| **GPU** *(Usage, Temp, VRAM)* | `MSI Afterburner` | Direct from Shared Memory |
| **System RAM** | `Windows OS` | Native OS query (Shows total PC usage) |
| **Dashboard Clock** | `Mobile Browser` | Synced locally to your phone's time |

</details>

<details>
<summary><b>🛠️ For Developers: Build from Source (Click to Expand)</b></summary>
<br>

If you prefer to inspect the code, modify the dashboard UI, or compile the executable yourself:

```bash
# 1. Clone the repository
git clone [https://github.com/HusseinWalid48/SysCast.git](https://github.com/HusseinWalid48/SysCast.git)
cd SysCast

# 2. Install required Python libraries
pip install fastapi uvicorn websockets psutil qrcode pillow pystray

# 3. Run the script directly
python SysCast.py

# 4. Compile to a standalone .exe (Optional)
python -m pyinstaller --onefile SysCast.py
```
</details>

<details>
<summary><b>❓ Troubleshooting / FAQ (Click to Expand)</b></summary>
<br>

* **My phone won't connect after scanning the QR code?**
  Make sure both your PC and phone are on the exact same local network (e.g., PC on Ethernet and Phone on the same router's Wi-Fi). If Windows Firewall pops up on first launch, make sure to click **Allow Access**.
* **Why are some sensors missing on my phone?**
  SysCast only displays sensors that are actively checked (`✔️`) inside MSI Afterburner's *Monitoring* settings. Open Afterburner, enable the sensor, and it will appear automatically.
</details>

---

<div align="center">
  <sub>Built by <a href="https://github.com/HusseinWalid48">HusseinWalid48</a> • Distributed under the MIT License.</sub>
</div>
