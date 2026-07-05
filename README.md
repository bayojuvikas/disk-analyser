# Disk Space Analyzer & Smart Cleaner

A lightning-fast, premium web-based local disk space analyzer, duplicate finder, and smart system cleaner, built entirely with Python and Vanilla JavaScript/CSS. 

This project aims to provide a native-feeling, high-performance web dashboard that can traverse local drives (like `C:\` and `D:\`), process millions of files in seconds via Windows Search Index integration or a Deep Crawler, and present the user with an aesthetically stunning, actionable dashboard to clean up disk space.

## ?? Features

* **High-Speed Drive Scanning**: Uses the Windows Search Index (`ADODB` query) to instantly find large files, falling back to an optimized multi-threaded `os.scandir` crawler for deep analysis.
* **Smart Filtering & Search**: Natural language filtering for semantic categories (e.g. searching "videos" matches `.mp4`, `.mkv`, etc.), along with drive and size-bracket filters.
* **Folder Explorer**: Understand exactly what directories are consuming space with a size-sorted interactive folder tree.
* **Duplicate Finder**: Reclaim space effortlessly. The duplicate finder groups files by size and utilizes partial cryptographic hashing (`hashlib.md5`) to guarantee bit-for-bit accuracy without slowing down your system.
* **Smart Cleaner**: Safely targets and calculates space used by temporary system files, Windows update caches, old downloads, and browser caches for instant 1-click clearance.
* **High-Performance Transfer Engine**: Securely move or copy files to backup drives with optimized 8MB I/O buffers for maximum SSD throughput.
* **Safety First**: Incorporates strict safeguards against deleting critical OS files and handles cloud-only files (like OneDrive stubs) gracefully to prevent systemic errors.

## ?? Tech Stack

* **Backend**: Python 3 standard library (`http.server`, `threading`, `os`, `shutil`, `json`). Zero external pip dependencies! 
* **Frontend**: Vanilla JavaScript (ES6) and HTML5.
* **Styling**: Premium custom CSS variables featuring a glassmorphism design system, smooth micro-animations, and responsive dashboard charts.

## ?? How to Run Locally

1. Clone this repository.
2. Ensure you have Python 3.7+ installed.
3. Open a terminal and run the server script:
   ```bash
   python app.py
   ```
4. A browser window will automatically launch and point to `http://localhost:8765/`.

## ?? Screenshots & UI

*(Add screenshots of the Large Files Finder, Charts, and Smart Cleaner here!)*

## ??? Architecture Note

The backend runs a lightweight HTTP server on port 8765. The frontend polls `/api/status` to get real-time disk scan percentages and memory usage. File deletion is handled defensively by ensuring the target path isn't protected (`is_protected`), followed by UI state reconciliation.

---
*Built with passion.*
