<div align="center">
  
# 🚀 Disk Space Analyzer & Premium Cleaner Suite
**A lightning-fast, highly optimized local drive analyzer and smart system cleaner.**

[![Python](https://img.shields.io/badge/Python-3.7+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Vanilla JS](https://img.shields.io/badge/JavaScript-ES6-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[![HTML5 & CSS3](https://img.shields.io/badge/UI-Glassmorphism-FF4B4B?style=for-the-badge&logo=html5&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/HTML)
[![License: MIT](https://img.shields.io/badge/License-MIT-success?style=for-the-badge)](#)

</div>

<br>

Welcome to the **Disk Space Analyzer**, a native-feeling, high-performance web dashboard designed to traverse your local drives, process millions of files in seconds, and present you with an aesthetically stunning, actionable interface to reclaim your disk space.

Built strictly with Python and Vanilla JavaScript—**zero external pip dependencies required**.

---

## ✨ Key Features

### ⚡ Ultra-Fast Drive Scanning
Leverages the native Windows Search Index (`ADODB` query) to instantly surface large files across your drives. Falls back to an optimized, multi-threaded `os.scandir` crawler for deep, thorough analysis.

### 🧠 Smart Semantic Search & Filtering
Don't just search by extension—search by meaning. Type "videos" to instantly filter `.mp4`, `.mkv`, and `.mov`, or filter dynamically by drive letter and precise size brackets (e.g., `> 1 GB`, `100MB - 500MB`).

### 👯 Duplicate Finder
Reclaim gigabytes of wasted space effortlessly. The duplicate engine groups files by size and utilizes partial cryptographic hashing (`hashlib.md5`) to guarantee bit-for-bit accuracy without bogging down your system.

### 🧹 Smart System Cleaner
Safely targets and calculates space used by temporary system files, Windows update caches, old downloads, and browser caches (Chrome/Edge) for instant, 1-click clearance.

### 🚀 High-Performance Transfer Engine
Need to back up large files before deleting? Securely move or copy files to backup drives with highly optimized 8MB I/O buffers tailored for maximum SSD throughput.

### 🛡️ Safety & System Integrity
Strict safeguards prevent the deletion of critical OS files. Gracefully handles cloud-only files (like OneDrive stubs) to prevent system errors or accidental downloads.

---

## 🛠️ Tech Stack

* **Backend Engine:** Python 3 standard library (`http.server`, `threading`, `os`, `shutil`, `json`). Lightweight and blazingly fast.
* **Frontend UI:** Vanilla JavaScript (ES6) and HTML5.
* **Aesthetics:** Custom CSS variables featuring a premium glassmorphism design system, smooth micro-animations, and interactive dashboard charts.

---

## 🚀 Getting Started

You can run this tool entirely locally on your Windows machine in just a few seconds.

### Prerequisites
* Windows OS (due to Windows Search Index integrations)
* Python 3.7 or higher

### Installation & Usage

1. **Clone the repository:**
   ```bash
   git clone https://github.com/bayojuvikas/disk-analyser.git
   cd disk-analyser
   ```

2. **Run the local server:**
   ```bash
   python app.py
   ```

3. **Open the Dashboard:**
   A browser window will automatically launch and point to `http://localhost:8765/`. Start scanning!

---

## 🏗️ Architecture Notes

The backend runs a lightweight, threaded HTTP server on port `8765`. 
The frontend maintains a real-time connection, polling `/api/status` to fetch disk scan percentages, active directories, and memory usage. File deletion is handled defensively by ensuring the target path isn't protected via the internal `is_protected()` routing, followed by instant UI state reconciliation.

<br>

<div align="center">
  <i>Built with passion.</i>
</div>
