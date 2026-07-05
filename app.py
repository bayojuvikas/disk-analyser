import os
import sys
import json
import threading
import time
import shutil
import webbrowser
import socket
import subprocess
import queue
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler

# Scanner settings and state
SCAN_LIMIT = 5000  # Max files to keep in memory (all sizes now included)
DUPLICATE_MIN_SIZE = 5 * 1024 * 1024  # 5MB min size for duplicate analysis to save memory

scan_state = {
    "scanning": False,
    "scanned_files": 0,
    "current_dir": "",
    "stop_requested": False,
    "largest_files": [],
    "folder_sizes": {},       # Map: folder_path -> cumulative_size
    "all_files_for_dup": []   # List of dicts: {"path": ..., "name": ..., "size": ...}
}

# High-Performance Transfer Engine State
copy_state = {
    "running": False,
    "src_list": [],
    "dest": "",
    "operation": "",  # "copy" or "move"
    "bytes_copied": 0,
    "total_bytes": 0,
    "speed_mbs": 0.0,
    "eta_seconds": 0.0,
    "cancel_requested": False,
    "error": None,
    "done": False,
    "current_file": ""
}

lock = threading.Lock()
dup_lock = threading.Lock()
copy_lock = threading.Lock()

# Protected paths where deletion is strictly forbidden to prevent system corruption
PROTECTED_SUBSTRINGS = [
    "\\windows\\", 
    "\\$recycle.bin\\", 
    "\\system volume information\\",
    "\\boot\\",
    "\\efi\\",
    "\\msocache\\",
    "\\programdata\\microsoft\\"
]

PROTECTED_EXACT_NAMES = [
    "pagefile.sys",
    "hiberfil.sys",
    "swapfile.sys",
    "ntldr",
    "bootmgr",
    "bootsect.bak"
]

def is_protected(filepath):
    path_lower = filepath.lower()
    for sub in PROTECTED_SUBSTRINGS:
        if sub in path_lower:
            return True
    name = os.path.basename(path_lower)
    if name in PROTECTED_EXACT_NAMES:
        return True
    return False

def get_drive_stats(drive_letter):
    drive_path = f"{drive_letter}:\\"
    if not os.path.exists(drive_path):
        return {"exists": False}
    try:
        total, used, free = shutil.disk_usage(drive_path)
        percent_used = round((used / total) * 100, 1) if total > 0 else 0
        return {
            "exists": True,
            "total": total,
            "used": used,
            "free": free,
            "percent_used": percent_used
        }
    except Exception:
        return {"exists": False}

def update_folder_sizes_in_map(filepath, size):
    norm_path = os.path.normpath(filepath)
    parts = norm_path.split('\\')
    for i in range(1, len(parts)):
        parent = '\\'.join(parts[:i])
        if parent:
            if parent.endswith(':'):
                parent += '\\'
            parent_key = os.path.normpath(parent).lower()
            with lock:
                scan_state["folder_sizes"][parent_key] = scan_state["folder_sizes"].get(parent_key, 0) + size

def query_windows_search_index():
    ps_script = """
    $c = New-Object -ComObject ADODB.Connection
    try {
        $c.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        $r = New-Object -ComObject ADODB.Recordset
        $sql = "SELECT System.ItemPathDisplay, System.ItemName, System.Size FROM SystemIndex WHERE SCOPE='file:' AND System.Size IS NOT NULL ORDER BY System.Size DESC"
        $r.Open($sql, $c)
        while(!$r.EOF){
            $path = $r.Fields.Item('System.ItemPathDisplay').Value
            $name = $r.Fields.Item('System.ItemName').Value
            $size = $r.Fields.Item('System.Size').Value
            if ($path -and $name -and $size) {
                Write-Output "$path||$name||$size"
            }
            $r.MoveNext()
        }
        $r.Close()
    } catch {}
    finally {
        $c.Close()
    }
    """
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    process = subprocess.Popen(
        ["powershell", "-NoProfile", "-Command", ps_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        startupinfo=startupinfo
    )
    stdout, _ = process.communicate()
    
    results = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or "||" not in line:
            continue
        parts = line.split("||")
        if len(parts) == 3:
            path, name, size_str = parts
            try:
                size = int(size_str)
                if not is_protected(path):
                    drive = path[0].upper() if len(path) > 1 and path[1] == ':' else 'C'
                    results.append({
                        "path": path,
                        "name": name,
                        "size": size,
                        "drive": drive
                    })
                    update_folder_sizes_in_map(path, size)
                    if size >= DUPLICATE_MIN_SIZE:
                        with dup_lock:
                            scan_state["all_files_for_dup"].append({
                                "path": path,
                                "name": name,
                                "size": size
                            })
            except ValueError:
                continue
    return results

def scan_drives(mode="fast", drives=None):
    global scan_state
    
    with lock:
        scan_state["scanning"] = True
        scan_state["scanned_files"] = 0
        scan_state["stop_requested"] = False
        scan_state["largest_files"] = []
        scan_state["folder_sizes"] = {}
    with dup_lock:
        scan_state["all_files_for_dup"] = []

    if mode == "fast":
        with lock:
            scan_state["current_dir"] = "Indexing files using Windows Search..."
        
        results = query_windows_search_index()
        
        # If drive filter specified, restrict results to those drives
        if drives:
            drives_upper = [d.upper() for d in drives]
            results = [r for r in results if r.get("drive", "").upper() in drives_upper]
        
        with lock:
            scan_state["largest_files"] = results[:SCAN_LIMIT]
            scan_state["scanned_files"] = len(results)
            scan_state["scanning"] = False
            scan_state["current_dir"] = ""
        return
    
    # Determine which drives to physically scan
    if drives:
        candidates = [d.upper() for d in drives]
    else:
        candidates = ["C", "D"]
    drives_to_scan = []
    for d in candidates:
        if os.path.exists(f"{d}:\\"):
            drives_to_scan.append(f"{d}:\\")
            
    temp_largest = []
    temp_lock = threading.Lock()
    
    EXCLUDE_DIRS = {
        "windows", "program files", "program files (x86)", "programdata",
        "system volume information", "$recycle.bin", "recovery", "config.msi"
    }

    dir_queue = queue.Queue()
    for d in drives_to_scan:
        dir_queue.put(d)
        
    active_workers = 0
    active_workers_lock = threading.Lock()
    
    def worker():
        nonlocal active_workers
        while not scan_state["stop_requested"]:
            try:
                current_dir = dir_queue.get(timeout=0.1)
                with active_workers_lock:
                    active_workers += 1
            except queue.Empty:
                with active_workers_lock:
                    if active_workers == 0:
                        break
                continue

            try:
                with lock:
                    scan_state["current_dir"] = current_dir
                
                with os.scandir(current_dir) as it:
                    for entry in it:
                        if scan_state["stop_requested"]:
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                name_lower = entry.name.lower()
                                if (name_lower not in EXCLUDE_DIRS and 
                                    not entry.name.startswith('$') and 
                                    not entry.name.startswith('.')):
                                    dir_queue.put(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                file_lower = entry.name.lower()
                                if (file_lower in PROTECTED_EXACT_NAMES or 
                                    file_lower.endswith('.sys') or 
                                    entry.name.startswith('$') or 
                                    entry.name.startswith('.')):
                                    continue
                                    
                                with lock:
                                    scan_state["scanned_files"] += 1
                                    
                                size = entry.stat(follow_symlinks=False).st_size
                                update_folder_sizes_in_map(entry.path, size)
                                
                                if size >= DUPLICATE_MIN_SIZE:
                                    with dup_lock:
                                        scan_state["all_files_for_dup"].append({
                                            "path": entry.path,
                                            "name": entry.name,
                                            "size": size
                                        })

                                with temp_lock:
                                    temp_largest.append({
                                        "path": entry.path,
                                        "name": entry.name,
                                        "size": size,
                                        "drive": entry.path[0].upper()
                                    })
                                    if len(temp_largest) > SCAN_LIMIT * 2:
                                        temp_largest.sort(key=lambda x: x["size"], reverse=True)
                                        del temp_largest[SCAN_LIMIT:]
                        except OSError:
                            continue
            except OSError:
                pass
            finally:
                dir_queue.task_done()
                with active_workers_lock:
                    active_workers -= 1

    threads = []
    num_threads = 12
    for _ in range(num_threads):
        t = threading.Thread(target=worker)
        t.start()
        threads.append(t)
        
    while any(t.is_alive() for t in threads) and not scan_state["stop_requested"]:
        with temp_lock:
            temp_largest.sort(key=lambda x: x["size"], reverse=True)
            current_top = list(temp_largest[:SCAN_LIMIT])
        with lock:
            scan_state["largest_files"] = current_top
        time.sleep(0.5)
        
    with temp_lock:
        temp_largest.sort(key=lambda x: x["size"], reverse=True)
        final_top = list(temp_largest[:SCAN_LIMIT])
    with lock:
        scan_state["largest_files"] = final_top
        scan_state["scanning"] = False
        scan_state["current_dir"] = ""

# --- Duplicate File Matching Algorithm ---

def get_fast_hash(filepath):
    try:
        with open(filepath, 'rb') as f:
            return hashlib.md5(f.read(128 * 1024)).hexdigest()
    except Exception:
        return None

def find_duplicate_groups():
    with dup_lock:
        all_files = list(scan_state["all_files_for_dup"])
        
    size_groups = {}
    for f in all_files:
        size_groups.setdefault(f["size"], []).append(f)
        
    potential_dups = {size: files for size, files in size_groups.items() if len(files) > 1}
    dup_groups = []
    
    for size, files in potential_dups.items():
        hash_groups = {}
        for f in files:
            f_hash = get_fast_hash(f["path"])
            if f_hash:
                hash_groups.setdefault(f_hash, []).append(f)
                
        for f_hash, dup_list in hash_groups.items():
            if len(dup_list) > 1:
                dup_groups.append({
                    "size": size,
                    "hash": f_hash,
                    "files": dup_list
                })
                
    dup_groups.sort(key=lambda x: x["size"], reverse=True)
    return dup_groups

# --- High-Performance Copy/Move Thread Engine ---

def is_dir_path(path):
    if not path:
        return False
    if os.path.isdir(path):
        return True
    if path.endswith('\\') or path.endswith('/'):
        return True
    drive, tail = os.path.splitdrive(path)
    if tail in ('', '\\', '/'):
        return True
    filename = os.path.basename(path)
    if not filename or '.' not in filename:
        return True
    return False

def run_copy_thread(src_list, dest_dir, operation):
    global copy_state
    src_list = [s.strip().strip('"\'').strip() for s in src_list if s]
    dest_dir = dest_dir.strip().strip('"\'').strip()
    is_bulk = len(src_list) > 1

    FILE_ATTR_OFFLINE = 0x1000
    FILE_ATTR_RECALL_ON_DATA_ACCESS = 0x400000

    def is_cloud_only(path):
        try:
            st = os.stat(path)
            attrs = getattr(st, 'st_file_attributes', 0)
            return bool(attrs & FILE_ATTR_OFFLINE) or bool(attrs & FILE_ATTR_RECALL_ON_DATA_ACCESS)
        except Exception:
            return False

    skipped_cloud = []
    failed_files = []

    try:
        # Pre-classify all sources into local vs cloud-only
        local_srcs = []
        for s in src_list:
            if not os.path.exists(s) or os.path.isdir(s):
                continue
            if is_cloud_only(s):
                skipped_cloud.append(os.path.basename(s))
                print(f"[SKIP] Cloud-only placeholder: {s}")
            else:
                local_srcs.append(s)

        total_size = sum(os.path.getsize(s) for s in local_srcs)

        with copy_lock:
            copy_state["total_bytes"] = total_size
            copy_state["bytes_copied"] = 0
            copy_state["speed_mbs"] = 0.0
            copy_state["eta_seconds"] = 0
            copy_state["cancel_requested"] = False
            copy_state["error"] = None
            copy_state["done"] = False
            copy_state["src_list"] = local_srcs
            copy_state["dest"] = dest_dir
            copy_state["current_file"] = ""

        if not local_srcs:
            with copy_lock:
                copy_state["done"] = True
                if skipped_cloud:
                    copy_state["error"] = (
                        f"All {len(skipped_cloud)} selected files are OneDrive cloud-only placeholders "
                        f"(not stored locally). Open OneDrive and mark them 'Always keep on this device' first."
                    )
                else:
                    copy_state["error"] = "No valid local source files found for transfer."
            return

        start_time = time.time()
        chunk_size = 8 * 1024 * 1024  # 8MB buffer

        for s in local_srcs:
            if copy_state["cancel_requested"]:
                break

            with copy_lock:
                copy_state["current_file"] = s

            filename = os.path.basename(s)
            file_size = os.path.getsize(s)

            # Resolve destination
            if is_bulk or is_dir_path(dest_dir):
                dest_file = os.path.join(dest_dir, filename)
            else:
                dest_file = dest_dir

            dest_file_dir = os.path.dirname(dest_file)
            if dest_file_dir:
                os.makedirs(dest_file_dir, exist_ok=True)

            # Copy with 8MB buffered streams
            file_size = os.path.getsize(s)
            copied_successfully = False
            file_copied_bytes = 0
            try:
                with open(s, 'rb') as fsrc:
                    with open(dest_file, 'wb') as fdst:
                        while True:
                            if copy_state["cancel_requested"]:
                                break
                            chunk = fsrc.read(chunk_size)
                            if not chunk:
                                break
                            fdst.write(chunk)
                            file_copied_bytes += len(chunk)
                            with copy_lock:
                                copy_state["bytes_copied"] += len(chunk)
                                elapsed = time.time() - start_time
                                if elapsed > 0:
                                    speed = copy_state["bytes_copied"] / elapsed
                                    copy_state["speed_mbs"] = round(speed / (1024 * 1024), 2)
                                    remaining_bytes = copy_state["total_bytes"] - copy_state["bytes_copied"]
                                    copy_state["eta_seconds"] = int(remaining_bytes / speed) if speed > 0 else 0
                        if not copy_state["cancel_requested"]:
                            copied_successfully = True
            except OSError as e:
                print(f"[ERROR] Copy failed for {filename}: {e}")
                failed_files.append(filename)
                if file_copied_bytes > 0:
                    with copy_lock:
                        copy_state["bytes_copied"] -= file_copied_bytes
                if os.path.exists(dest_file):
                    try:
                        os.remove(dest_file)
                    except Exception:
                        pass
                with copy_lock:
                    copy_state["total_bytes"] = max(0, copy_state["total_bytes"] - file_size)
                continue  # Don't crash — move on to next file

            if copy_state["cancel_requested"]:
                if os.path.exists(dest_file):
                    try:
                        os.remove(dest_file)
                    except Exception:
                        pass
                break

            if operation == "move" and copied_successfully:
                if os.path.exists(dest_file) and os.path.getsize(dest_file) == os.path.getsize(s):
                    os.remove(s)
                    with lock:
                        scan_state["largest_files"] = [f for f in scan_state["largest_files"] if f["path"] != s]
                else:
                    failed_files.append(filename)
                    print(f"[ERROR] Move verification failed for {filename}")

        if copy_state["cancel_requested"]:
            return

        # Build summary of any issues
        warnings = []
        if skipped_cloud:
            names = ', '.join(skipped_cloud[:3]) + ('...' if len(skipped_cloud) > 3 else '')
            warnings.append(f"{len(skipped_cloud)} OneDrive cloud file(s) skipped (not on disk): {names}")
        if failed_files:
            names = ', '.join(failed_files[:3]) + ('...' if len(failed_files) > 3 else '')
            warnings.append(f"{len(failed_files)} file(s) failed: {names}")

        with copy_lock:
            copy_state["done"] = True
            if warnings:
                copy_state["error"] = " | ".join(warnings)
    except Exception as e:
        import traceback
        traceback.print_exc()
        with copy_lock:
            copy_state["error"] = str(e)
        if 'dest_file' in locals() and os.path.exists(dest_file):
            try:
                os.remove(dest_file)
            except Exception:
                pass
    finally:
        with copy_lock:
            copy_state["running"] = False

# --- Smart Cleaner Logic ---

def get_dir_size(path):
    total = 0
    if not os.path.exists(path):
        return 0
    try:
        for root, dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    continue
    except OSError:
        pass
    return total

def clear_dir(path):
    bytes_freed = 0
    if not os.path.exists(path):
        return 0
    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            fp = os.path.join(root, f)
            try:
                size = os.path.getsize(fp)
                os.remove(fp)
                bytes_freed += size
            except OSError:
                pass
        for d in dirs:
            dp = os.path.join(root, d)
            try:
                os.rmdir(dp)
            except OSError:
                pass
    return bytes_freed

def get_smart_clean_stats():
    user_profile = os.environ.get('USERPROFILE', '')
    temp_dir = os.environ.get('TEMP', '')
    
    user_temp_size = get_dir_size(temp_dir)
    sys_temp_size = get_dir_size("C:\\Windows\\Temp")
    
    chrome_cache = os.path.join(user_profile, "AppData", "Local", "Google", "Chrome", "User Data", "Default", "Cache")
    edge_cache = os.path.join(user_profile, "AppData", "Local", "Microsoft", "Edge", "User Data", "Default", "Cache")
    browser_size = get_dir_size(chrome_cache) + get_dir_size(edge_cache)
    
    downloads_path = os.path.join(user_profile, "Downloads")
    old_downloads_size = 0
    now = time.time()
    thirty_days_sec = 30 * 24 * 60 * 60
    
    if os.path.exists(downloads_path):
        try:
            for f in os.listdir(downloads_path):
                fp = os.path.join(downloads_path, f)
                if os.path.isfile(fp):
                    try:
                        mtime = os.path.getmtime(fp)
                        if now - mtime > thirty_days_sec:
                            old_downloads_size += os.path.getsize(fp)
                    except OSError:
                        continue
        except OSError:
            pass
            
    return {
        "user_temp": user_temp_size,
        "system_temp": sys_temp_size,
        "browser_cache": browser_size,
        "old_downloads": old_downloads_size
    }

def run_smart_clean(targets):
    user_profile = os.environ.get('USERPROFILE', '')
    temp_dir = os.environ.get('TEMP', '')
    bytes_freed = 0
    
    if "user_temp" in targets and temp_dir:
        bytes_freed += clear_dir(temp_dir)
    if "system_temp" in targets:
        bytes_freed += clear_dir("C:\\Windows\\Temp")
    if "browser_cache" in targets:
        chrome_cache = os.path.join(user_profile, "AppData", "Local", "Google", "Chrome", "User Data", "Default", "Cache")
        edge_cache = os.path.join(user_profile, "AppData", "Local", "Microsoft", "Edge", "User Data", "Default", "Cache")
        bytes_freed += clear_dir(chrome_cache)
        bytes_freed += clear_dir(edge_cache)
        
    if "old_downloads" in targets:
        downloads_path = os.path.join(user_profile, "Downloads")
        now = time.time()
        thirty_days_sec = 30 * 24 * 60 * 60
        if os.path.exists(downloads_path):
            try:
                for f in os.listdir(downloads_path):
                    fp = os.path.join(downloads_path, f)
                    if os.path.isfile(fp):
                        try:
                            mtime = os.path.getmtime(fp)
                            if now - mtime > thirty_days_sec:
                                size = os.path.getsize(fp)
                                os.remove(fp)
                                bytes_freed += size
                        except OSError:
                            continue
            except OSError:
                pass
    return bytes_freed

# --- API Router and Handler ---

class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path == "/":
            self.serve_file("index.html", "text/html")
        elif self.path == "/style.css":
            self.serve_file("style.css", "text/css")
        elif self.path == "/api/status":
            self.send_json_response({
                "scanning": scan_state["scanning"],
                "progress": {
                    "scanned_files": scan_state["scanned_files"],
                    "current_dir": scan_state["current_dir"]
                },
                "drives": {
                    "C": get_drive_stats("C"),
                    "D": get_drive_stats("D")
                }
            })
        elif self.path == "/api/files":
            with lock:
                files = list(scan_state["largest_files"])
            self.send_json_response({"files": files})
        elif self.path == "/api/duplicates":
            groups = find_duplicate_groups()
            self.send_json_response({"groups": groups})
        elif self.path == "/api/smart-clean/scan":
            stats = get_smart_clean_stats()
            self.send_json_response(stats)
        elif self.path == "/api/copy-move/status":
            with copy_lock:
                status = dict(copy_state)
            self.send_json_response(status)
        elif self.path.startswith("/api/folder-tree"):
            from urllib.parse import urlparse, parse_qs
            parsed_url = urlparse(self.path)
            query = parse_qs(parsed_url.query)
            target_path = query.get("path", [""])[0]
            
            if not target_path:
                roots = []
                for d in ["C", "D"]:
                    if os.path.exists(f"{d}:\\"):
                        stats = get_drive_stats(d)
                        roots.append({
                            "path": f"{d}:\\",
                            "name": f"Local Disk ({d}:)",
                            "is_dir": True,
                            "size": stats.get("used", 0)
                        })
                self.send_json_response({"children": roots})
                return

            if not os.path.exists(target_path):
                self.send_json_response({"error": "Path not found"}, status_code=404)
                return

            children = []
            try:
                with os.scandir(target_path) as it:
                    for entry in it:
                        try:
                            if is_protected(entry.path):
                                continue
                            
                            is_dir = entry.is_dir(follow_symlinks=False)
                            if is_dir:
                                size = scan_state["folder_sizes"].get(os.path.normpath(entry.path).lower(), 0)
                            else:
                                size = entry.stat(follow_symlinks=False).st_size
                                
                            children.append({
                                "path": entry.path,
                                "name": entry.name,
                                "is_dir": is_dir,
                                "size": size
                            })
                        except OSError:
                            continue
            except OSError:
                pass
                
            children.sort(key=lambda x: x["size"], reverse=True)
            self.send_json_response({"children": children})
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else ""

        if self.path == "/api/scan/start":
            if not scan_state["scanning"]:
                mode = "fast"
                drives = None  # None = scan all drives
                try:
                    params = json.loads(post_data) if post_data else {}
                    mode = params.get("mode", "fast")
                    drive_param = params.get("drive", "all")
                    if drive_param and drive_param != "all":
                        drives = [drive_param.upper()]
                except Exception:
                    pass
                threading.Thread(target=scan_drives, args=(mode, drives), daemon=True).start()
            self.send_json_response({"success": True})
        elif self.path == "/api/scan/stop":
            with lock:
                scan_state["stop_requested"] = True
            self.send_json_response({"success": True})
        elif self.path == "/api/copy-move/start":
            try:
                params = json.loads(post_data)
                print("COPY START PARAMETERS:", params)
                src = params.get("src")
                src_list = params.get("src_list")
                dest = params.get("dest")
                operation = params.get("operation", "copy")
                
                # Sanitize paths: strip surrounding quotes and spacing
                if src:
                    src = src.strip().strip('"\'').strip()
                if src_list:
                    src_list = [s.strip().strip('"\'').strip() for s in src_list if s]
                if dest:
                    dest = dest.strip().strip('"\'').strip()
                
                if src_list is None or len(src_list) == 0:
                    if src:
                        src_list = [src]
                    else:
                        src_list = []

                if not src_list:
                    self.send_json_response({"success": False, "error": "No source files specified."})
                    return

                for s in src_list:
                    if not os.path.exists(s):
                        self.send_json_response({"success": False, "error": f"Source file not found: {s}"})
                        return
                    if is_protected(s):
                        self.send_json_response({"success": False, "error": f"Protected path restricted: {s}"})
                        return

                if is_protected(dest):
                    self.send_json_response({"success": False, "error": "Protected destination path restricted."})
                    return
                
                with copy_lock:
                    if copy_state["running"]:
                        self.send_json_response({"success": False, "error": "Another transfer task is currently running."})
                        return
                    copy_state["running"] = True
                    copy_state["src_list"] = src_list
                    copy_state["dest"] = dest
                    copy_state["operation"] = operation
                
                threading.Thread(target=run_copy_thread, args=(src_list, dest, operation), daemon=True).start()
                self.send_json_response({"success": True})
            except Exception as e:
                self.send_json_response({"success": False, "error": str(e)})
        elif self.path == "/api/copy-move/cancel":
            with copy_lock:
                copy_state["cancel_requested"] = True
            self.send_json_response({"success": True})
        elif self.path == "/api/explore":
            try:
                params = json.loads(post_data)
                filepath = params.get("path")
                if filepath and os.path.exists(filepath):
                    subprocess.Popen(f'explorer /select,"{filepath}"')
                    self.send_json_response({"success": True})
                else:
                    self.send_json_response({"success": False, "error": "File not found."})
            except Exception as e:
                self.send_json_response({"success": False, "error": str(e)})
        elif self.path == "/api/delete":
            try:
                params = json.loads(post_data)
                filepath = params.get("path")
                if not filepath or not os.path.exists(filepath):
                    self.send_json_response({"success": False, "error": "File does not exist."})
                    return
                if is_protected(filepath):
                    self.send_json_response({"success": False, "error": "Protected path."})
                    return
                
                if os.path.isdir(filepath):
                    shutil.rmtree(filepath)
                else:
                    os.remove(filepath)
                    
                norm_fp = os.path.normpath(filepath).lower()
                with lock:
                    scan_state["largest_files"] = [f for f in scan_state["largest_files"] if os.path.normpath(f["path"]).lower() != norm_fp]
                    scan_state["all_files_for_dup"] = [f for f in scan_state["all_files_for_dup"] if os.path.normpath(f["path"]).lower() != norm_fp]
                self.send_json_response({"success": True})
            except Exception as e:
                self.send_json_response({"success": False, "error": str(e)})
        elif self.path == "/api/delete-bulk":
            try:
                params = json.loads(post_data)
                filepaths = params.get("paths", [])
                deleted_count = 0
                errors = []
                for filepath in filepaths:
                    if not os.path.exists(filepath):
                        continue
                    if is_protected(filepath):
                        errors.append(f"Protected: {filepath}")
                        continue
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                        norm_fp = os.path.normpath(filepath).lower()
                        with lock:
                            scan_state["largest_files"] = [f for f in scan_state["largest_files"] if os.path.normpath(f["path"]).lower() != norm_fp]
                            scan_state["all_files_for_dup"] = [f for f in scan_state["all_files_for_dup"] if os.path.normpath(f["path"]).lower() != norm_fp]
                    except Exception as ex:
                        errors.append(str(ex))
                self.send_json_response({
                    "success": len(errors) == 0,
                    "deleted_count": deleted_count,
                    "error": ", ".join(errors) if errors else None
                })
            except Exception as e:
                self.send_json_response({"success": False, "error": str(e)})
        elif self.path == "/api/smart-clean/execute":
            try:
                params = json.loads(post_data)
                targets = params.get("targets", [])
                bytes_freed = run_smart_clean(targets)
                self.send_json_response({"success": True, "bytes_freed": bytes_freed})
            except Exception as e:
                self.send_json_response({"success": False, "error": str(e)})
        else:
            self.send_error(404, "Not Found")

    def serve_file(self, filename, content_type):
        try:
            dir_path = os.path.dirname(os.path.realpath(__file__))
            file_path = os.path.join(dir_path, filename)
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            self.send_error(500, "Internal Server Error")

    def send_json_response(self, data, status_code=200):
        response_bytes = json.dumps(data).encode('utf-8')
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(response_bytes))
        self.end_headers()
        self.wfile.write(response_bytes)

def find_free_port(preferred=8765):
    # Try preferred fixed port first
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('localhost', preferred))
        s.close()
        return preferred
    except OSError:
        pass
    # Fall back to random free port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('localhost', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def main():
    port = find_free_port()
    server_address = ('localhost', port)
    httpd = HTTPServer(server_address, APIHandler)
    url = f"http://localhost:{port}/"
    print(f"Starting Disk Space Analyzer server on {url}")
    print(f"Open your browser and go to: {url}")
    threading.Thread(target=lambda: (time.sleep(1), webbrowser.open(url)), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()
        sys.exit(0)

if __name__ == "__main__":
    main()
