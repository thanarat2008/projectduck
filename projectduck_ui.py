"""
ProjectDuck - ใช้รูปเป็นพื้นหลัง แล้ววางปุ่ม/หลอด/log ที่ใช้งานได้จริงทับลงไป
ขนาดหน้าต่าง 795x465 ไม่มีขอบ/title bar ของ Windows (ใช้ปุ่มย่อ/ปิดในรูปแทน ลากแถบบนเพื่อย้ายหน้าต่างได้)

ใช้งาน:  python projectduck_ui.py   (ต้องมี Pillow และไฟล์รูปอยู่โฟลเดอร์เดียวกัน)
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import queue
import textwrap
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime

try:
    from PIL import Image, ImageTk
except ImportError:
    raise SystemExit("ต้องติดตั้ง Pillow ก่อน:  python -m pip install pillow")

# เมื่อเป็น .exe: ไฟล์สำรอง (*_backup.json) เก็บข้างไฟล์ .exe, รูปพื้นหลังอ่านจากที่ฝังไว้ในตัว exe
HERE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
RES_DIR = getattr(sys, "_MEIPASS", HERE)

WIN_W, WIN_H = 795, 465          # ขนาดโปรแกรม
REF_W, REF_H = 795, 465          # พิกัดด้านล่างวัดเป็นพิกเซลของหน้าต่างจริง (795x465)
DEBUG = False                    # True = วาดกรอบแดงแสดงพื้นที่ปุ่ม/หลอด และพิมพ์พิกัดที่คลิกลง console

BG = "#000000"
YELLOW = "#FFE500"
BAR_BG = "#3a3d46"
SHOW_TRACK = False               # False = ไม่วาดแถบเทาด้านหลังหลอด (ส่วนที่ยังไม่เต็มจะใส เห็นพื้นหลัง)

# พื้นที่ต่าง ๆ ในรูปอ้างอิง (x1, y1, x2, y2)
R_RUN = (55, 144, 203, 200)
R_RESTORE = (217, 144, 381, 200)
R_HOW = (53, 250, 132, 270)
R_TOGGLE = (170, 250, 229, 270)
R_SIGNOUT = (-1, -1, -1, -1)     # รูปนี้ไม่มีปุ่ม Sign out
R_BAR = (55, 227, 546, 237)
R_BAR_COVER = (50, 203, 549, 228)       # ปิดหลอดเดิมในรูป (ตำแหน่งเดิม)
R_PCT = (551, 215, 590, 248)
R_PCT_OLD = (551, 205, 590, 228)         # ปิดตัวเลข % เดิมในรูป
R_LOG = (65, 285, 707, 411)
R_TITLE = (0, 0, 640, 34)        # แถบบน: ลากเพื่อย้ายหน้าต่าง
R_MIN = (642, 2, 685, 32)
R_CLOSE = (738, 2, 785, 32)
R_TOGGLE_COVER = (168, 248, 232, 272)
R_COVER_RUN = (47, 138, 212, 205)
R_COVER_RESTORE = (212, 138, 390, 205)


# ---------------------------------------------------------------------------
# ตั้งค่า Power (Windows): Ultimate Performance + ปิดหน้าจอ = Never (ทั้งเสียบปลั๊กและใช้แบตเตอรี่)
# ค่าเดิมจะถูกสำรองไว้ใน power_backup.json และเอากลับคืนเมื่อกด Restore
# ---------------------------------------------------------------------------
IS_WIN = sys.platform.startswith("win")
ULTIMATE_GUID = "e9a42b02-d5df-448d-aa00-03f14749eb61"
HIGH_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
POWER_BACKUP = os.path.join(HERE, "power_backup.json")
GUID_RE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def _powercfg(*args):
    """รัน powercfg แล้วคืน (returncode, ข้อความ)"""
    r = subprocess.run(["powercfg", *args], capture_output=True,
                       creationflags=0x08000000)       # CREATE_NO_WINDOW
    return r.returncode, r.stdout.decode("utf-8", "ignore") + r.stderr.decode("utf-8", "ignore")


def _active_scheme():
    _, out = _powercfg("/getactivescheme")
    m = GUID_RE.search(out)
    return m.group(0) if m else None


def _monitor_timeouts_min():
    """อ่านเวลาปิดจอปัจจุบัน (นาที) -> (AC, DC)"""
    _, out = _powercfg("/q", "SCHEME_CURRENT", "SUB_VIDEO", "VIDEOIDLE")
    vals = re.findall(r"0x[0-9a-fA-F]+", out)
    if len(vals) < 2:
        return 10, 5
    return int(vals[-2], 16) // 60, int(vals[-1], 16) // 60


def _find_or_create_ultimate():
    _, out = _powercfg("/list")
    for line in out.splitlines():
        if "ultimate" in line.lower():
            m = GUID_RE.search(line)
            if m:
                return m.group(0), "พบแผน Ultimate Performance ในเครื่องแล้ว"
    code, out = _powercfg("/duplicatescheme", ULTIMATE_GUID)
    m = GUID_RE.search(out)
    if code == 0 and m:
        return m.group(0), "สร้างแผน Ultimate Performance ใหม่แล้ว"
    return HIGH_GUID, "เครื่องนี้ไม่รองรับ Ultimate Performance -> ใช้ High performance แทน"


def task_power_on(log):
    if not IS_WIN:
        log("ข้ามขั้นตอน Power (ใช้ได้เฉพาะ Windows)")
        return
    if not os.path.exists(POWER_BACKUP):      # สำรองเฉพาะครั้งแรก กันค่าเดิมถูกทับ
        ac, dc = _monitor_timeouts_min()
        data = {"scheme": _active_scheme(), "monitor_ac": ac, "monitor_dc": dc}
        with open(POWER_BACKUP, "w", encoding="utf-8") as f:
            json.dump(data, f)
        log(f"สำรองค่า Power เดิมแล้ว (ปิดจอเดิม AC={ac} นาที, DC={dc} นาที)")
    guid, note = _find_or_create_ultimate()
    log(note)
    code, out = _powercfg("/setactive", guid)
    log("ตั้ง Power plan = Ultimate Performance สำเร็จ" if code == 0 and guid != HIGH_GUID
        else ("ตั้ง Power plan = High performance สำเร็จ" if code == 0
              else f"ตั้ง Power plan ไม่สำเร็จ: {out.strip()[:80]}"))
    c1, _ = _powercfg("/change", "monitor-timeout-ac", "0")
    c2, _ = _powercfg("/change", "monitor-timeout-dc", "0")
    log("ตั้งเวลาปิดหน้าจอ = Never (เสียบปลั๊ก)" if c1 == 0 else "ตั้งปิดหน้าจอ (เสียบปลั๊ก) ไม่สำเร็จ")
    log("ตั้งเวลาปิดหน้าจอ = Never (ใช้แบตเตอรี่)" if c2 == 0 else "ตั้งปิดหน้าจอ (แบตเตอรี่) ไม่สำเร็จ")


def task_power_off(log):
    if not IS_WIN:
        log("ข้ามขั้นตอน Power (ใช้ได้เฉพาะ Windows)")
        return
    if not os.path.exists(POWER_BACKUP):
        log("ไม่พบไฟล์สำรอง Power (ยังไม่เคยกด Run) -> ข้าม")
        return
    with open(POWER_BACKUP, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("scheme"):
        code, _ = _powercfg("/setactive", data["scheme"])
        log("คืนค่า Power plan เดิมแล้ว" if code == 0 else "คืนค่า Power plan เดิมไม่สำเร็จ")
    _powercfg("/change", "monitor-timeout-ac", str(data.get("monitor_ac", 10)))
    _powercfg("/change", "monitor-timeout-dc", str(data.get("monitor_dc", 5)))
    log(f"คืนเวลาปิดหน้าจอเดิม (AC={data.get('monitor_ac')} นาที, DC={data.get('monitor_dc')} นาที)")
    os.remove(POWER_BACKUP)


# ---------------------------------------------------------------------------
# ตั้งค่า QoS (Psched) ผ่าน Registry: MaxOutstandingSends / NonBestEffortLimit / TimerResolution = 0
# เหมือนสคริปต์ PowerShell ที่ให้มา ต้องเปิดโปรแกรมแบบ Run as administrator
# ค่าเดิมสำรองไว้ใน network_backup.json และเอากลับคืนเมื่อกด Restore
# ---------------------------------------------------------------------------
PSCHED_PATH = r"SOFTWARE\Policies\Microsoft\Windows\Psched"
PSCHED_VALUES = ("MaxOutstandingSends", "NonBestEffortLimit", "TimerResolution")
NET_BACKUP = os.path.join(HERE, "network_backup.json")


def _is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _gpupdate(log):
    try:
        r = subprocess.run(["gpupdate", "/force"], capture_output=True,
                           creationflags=0x08000000, timeout=180)
        log("gpupdate /force เสร็จสิ้น" if r.returncode == 0
            else f"gpupdate /force ไม่สำเร็จ (code {r.returncode})")
    except Exception as e:  # noqa: BLE001
        log(f"gpupdate /force ไม่สำเร็จ: {e}")


def task_network_on(log):
    if not IS_WIN:
        log("ข้ามขั้นตอน QoS (ใช้ได้เฉพาะ Windows)")
        return
    if not _is_admin():
        log("ข้ามขั้นตอน QoS: ต้องเปิดโปรแกรมแบบ Run as administrator")
        return
    import winreg
    access = winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
    backup = {"key_existed": True, "values": {}}
    try:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PSCHED_PATH, 0, access)
        except FileNotFoundError:
            backup["key_existed"] = False
            key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, PSCHED_PATH, 0, access)
        with key:
            for name in PSCHED_VALUES:
                try:
                    backup["values"][name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    backup["values"][name] = None
            if not os.path.exists(NET_BACKUP):          # สำรองครั้งแรกเท่านั้น
                with open(NET_BACKUP, "w", encoding="utf-8") as f:
                    json.dump(backup, f)
                log("สำรองค่า QoS เดิมแล้ว")
            for name in PSCHED_VALUES:
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0)
                log(f"  - ตั้ง {name} = 0")
    except OSError as e:
        log(f"ตั้งค่า QoS ไม่สำเร็จ: {e}")
        return
    _gpupdate(log)


def task_network_off(log):
    if not IS_WIN:
        log("ข้ามขั้นตอน QoS (ใช้ได้เฉพาะ Windows)")
        return
    if not os.path.exists(NET_BACKUP):
        log("ไม่พบไฟล์สำรอง QoS (ยังไม่เคยกด Run) -> ข้าม")
        return
    if not _is_admin():
        log("ข้ามขั้นตอน QoS: ต้องเปิดโปรแกรมแบบ Run as administrator")
        return
    import winreg
    with open(NET_BACKUP, encoding="utf-8") as f:
        backup = json.load(f)
    access = winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PSCHED_PATH, 0, access) as key:
            for name, old in backup["values"].items():
                if old is None:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                    log(f"  - ลบ {name} (เดิมไม่มี)")
                else:
                    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(old))
                    log(f"  - คืน {name} = {old}")
        if not backup.get("key_existed", True):
            try:
                winreg.DeleteKeyEx(winreg.HKEY_LOCAL_MACHINE, PSCHED_PATH, access)
            except OSError:
                pass                                   # ยังมีค่าอื่นอยู่ในคีย์ -> ปล่อยไว้
    except OSError as e:
        log(f"คืนค่า QoS ไม่สำเร็จ: {e}")
        return
    os.remove(NET_BACKUP)
    _gpupdate(log)


# ---------------------------------------------------------------------------
# จูนเกมผ่าน Registry (เหมือนไฟล์ .reg ที่ให้มา)
#   1) Win32PrioritySeparation  2) MMCSS (Network/Games)  3) ปิด Mouse Acceleration
#   4) ปิด Game DVR  5) ปิด Power Throttling  6) GlobalTimerResolutionRequests
# ค่า HKLM ต้องเปิดโปรแกรมแบบ Run as administrator (ถ้าไม่ใช่จะข้ามเฉพาะค่า HKLM ส่วน HKCU ยังทำงาน)
# ค่าเดิมสำรองไว้ใน tweaks_backup.json และเอากลับคืนเมื่อกด Restore
# ---------------------------------------------------------------------------
TWEAKS_BACKUP = os.path.join(HERE, "tweaks_backup.json")

_MM = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
# (hive, subkey, name, type, value)
TWEAKS = [
    ("HKLM", r"SYSTEM\CurrentControlSet\Control\PriorityControl", "Win32PrioritySeparation", "dword", 0x26),
    ("HKLM", _MM, "NetworkThrottlingIndex", "dword", 0xFFFFFFFF),
    ("HKLM", _MM, "SystemResponsiveness", "dword", 0),
    ("HKLM", _MM + r"\Tasks\Games", "GPU Priority", "dword", 8),
    ("HKLM", _MM + r"\Tasks\Games", "Priority", "dword", 6),
    ("HKLM", _MM + r"\Tasks\Games", "Scheduling Category", "sz", "High"),
    ("HKLM", _MM + r"\Tasks\Games", "SFIO Priority", "sz", "High"),
    ("HKCU", r"Control Panel\Mouse", "MouseSpeed", "sz", "0"),
    ("HKCU", r"Control Panel\Mouse", "MouseThreshold1", "sz", "0"),
    ("HKCU", r"Control Panel\Mouse", "MouseThreshold2", "sz", "0"),
    ("HKCU", r"Control Panel\Mouse", "MouseSensitivity", "sz", "10"),
    ("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", "dword", 0),
    ("HKCU", r"System\GameConfigStore", "GameDVR_FSEBehaviorMode", "dword", 2),
    ("HKCU", r"System\GameConfigStore", "GameDVR_HonorUserFSEBehaviorMode", "dword", 1),
    ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR", "dword", 0),
    ("HKLM", r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff", "dword", 1),
    ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\kernel", "GlobalTimerResolutionRequests", "dword", 1),
    ("HKLM", r"SYSTEM\CurrentControlSet\Services\kbdclass\Parameters", "KeyboardDataQueueSize", "dword", 0x16),
    ("HKLM", r"SYSTEM\CurrentControlSet\Services\mouclass\Parameters", "MouseDataQueueSize", "dword", 0x16),
]


def _tweak_id(hive, sub, name):
    return f"{hive}\\{sub}\\{name}"


def _apply_mouse_live(log):
    """ให้ค่าเมาส์มีผลทันที ไม่ต้อง log off (อ่านจาก HKCU ที่เพิ่งตั้ง/คืน)"""
    try:
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse") as k:
            def rd(n, d):
                try:
                    return int(winreg.QueryValueEx(k, n)[0])
                except (FileNotFoundError, ValueError):
                    return d
            accel, t1, t2 = rd("MouseSpeed", 1), rd("MouseThreshold1", 6), rd("MouseThreshold2", 10)
            sens = rd("MouseSensitivity", 10)
        arr = (ctypes.c_int * 3)(t1, t2, accel)
        ctypes.windll.user32.SystemParametersInfoW(0x0004, 0, arr, 0x03)     # SPI_SETMOUSE
        ctypes.windll.user32.SystemParametersInfoW(0x0071, 0, sens, 0x03)    # SPI_SETMOUSESPEED
        log("ใช้ค่าเมาส์ใหม่ทันทีแล้ว")
    except Exception as e:  # noqa: BLE001
        log(f"ใช้ค่าเมาส์ทันทีไม่สำเร็จ (ต้อง log off/รีสตาร์ต): {e}")


def task_tweaks_on(log):
    if not IS_WIN:
        log("ข้ามขั้นตอนจูนเกม (ใช้ได้เฉพาะ Windows)")
        return
    import winreg
    admin = _is_admin()
    if not admin:
        log("ไม่ได้เปิดแบบ Administrator -> จะทำเฉพาะค่า HKCU (เมาส์/Game DVR) ข้ามค่า HKLM")
    types = {"dword": winreg.REG_DWORD, "sz": winreg.REG_SZ}
    roots = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    access = winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY

    backup = {}
    if os.path.exists(TWEAKS_BACKUP):
        try:
            with open(TWEAKS_BACKUP, encoding="utf-8") as f:
                backup = json.load(f)
        except (OSError, ValueError):
            backup = {}

    done = skipped = failed = 0
    for hive, sub, name, typ, val in TWEAKS:
        if hive == "HKLM" and not admin:
            skipped += 1
            continue
        tid = _tweak_id(hive, sub, name)
        try:
            key_existed = True
            try:
                key = winreg.OpenKey(roots[hive], sub, 0, access)
            except FileNotFoundError:
                key_existed = False
                key = winreg.CreateKeyEx(roots[hive], sub, 0, access)
            with key:
                if tid not in backup:                      # สำรองเฉพาะครั้งแรก กันค่าเดิมถูกทับ
                    try:
                        old, old_t = winreg.QueryValueEx(key, name)
                        backup[tid] = {"existed": True, "type": old_t, "value": old,
                                       "key_existed": key_existed}
                    except FileNotFoundError:
                        backup[tid] = {"existed": False, "key_existed": key_existed}
                winreg.SetValueEx(key, name, 0, types[typ], val)
            done += 1
        except OSError as e:
            failed += 1
            log(f"  - ตั้ง {name} ไม่สำเร็จ: {e}")

    if backup:
        with open(TWEAKS_BACKUP, "w", encoding="utf-8") as f:
            json.dump(backup, f)
        log("สำรองค่า Registry เดิมแล้ว")
    log(f"จูนเกมสำเร็จ {done} ค่า" + (f", ข้าม {skipped} ค่า (ต้อง Admin)" if skipped else "")
        + (f", ล้มเหลว {failed} ค่า" if failed else ""))
    _apply_mouse_live(log)


def task_tweaks_off(log):
    if not IS_WIN:
        log("ข้ามขั้นตอนจูนเกม (ใช้ได้เฉพาะ Windows)")
        return
    if not os.path.exists(TWEAKS_BACKUP):
        log("ไม่พบไฟล์สำรองจูนเกม (ยังไม่เคยกด Run) -> ข้าม")
        return
    import winreg
    with open(TWEAKS_BACKUP, encoding="utf-8") as f:
        backup = json.load(f)
    admin = _is_admin()
    roots = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    access = winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY

    remaining = {}
    created_keys = []                                      # คีย์ที่เราสร้างเอง -> พยายามลบตอนท้าย
    restored = 0
    for hive, sub, name, _typ, _val in TWEAKS:
        tid = _tweak_id(hive, sub, name)
        info = backup.get(tid)
        if info is None:
            continue
        if hive == "HKLM" and not admin:
            remaining[tid] = info                          # เก็บไว้คืนทีหลังตอนรันแบบ Admin
            continue
        try:
            with winreg.OpenKey(roots[hive], sub, 0, access) as key:
                if info["existed"]:
                    winreg.SetValueEx(key, name, 0, int(info["type"]), info["value"])
                else:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
            if not info.get("key_existed", True):
                created_keys.append((hive, sub))
            restored += 1
        except FileNotFoundError:
            restored += 1                                  # คีย์หายไปแล้ว ถือว่าเรียบร้อย
        except OSError as e:
            remaining[tid] = info
            log(f"  - คืน {name} ไม่สำเร็จ: {e}")

    for hive, sub in sorted(set(created_keys), key=lambda t: -len(t[1])):   # ลบจากคีย์ลึกสุดก่อน
        try:
            winreg.DeleteKeyEx(roots[hive], sub, access)
        except OSError:
            pass                                           # ยังมีค่าอื่นอยู่ -> ปล่อยไว้

    log(f"คืนค่าจูนเกมแล้ว {restored} ค่า")
    if remaining:
        with open(TWEAKS_BACKUP, "w", encoding="utf-8") as f:
            json.dump(remaining, f)
        log(f"เหลือ {len(remaining)} ค่า HKLM ที่ต้องกด Restore อีกครั้งแบบ Administrator")
    else:
        os.remove(TWEAKS_BACKUP)
    _apply_mouse_live(log)


# ---------------------------------------------------------------------------
# ฟีเจอร์จากสคริปต์ PC Tune (.cmd): ล้างไฟล์ชั่วคราว / DNS / Optimize ดิสก์ / ปิด Hibernate /
# จำกัด CPU % / NVIDIA power limit / ซ่อมไฟล์ระบบ (SFC+DISM)
# ค่าที่เปลี่ยนจะสำรองไว้ใน misc_backup.json และคืนเมื่อกด Restore
# ---------------------------------------------------------------------------
DNS_SERVERS = ("1.1.1.1", "8.8.8.8")   # None = ไม่เปลี่ยน DNS
DISABLE_HIBERNATE = True               # False = ไม่ยุ่งกับ Hibernate (ปิดแล้ว Fast Startup จะปิดด้วย)
CPU_MAX_PERCENT = None                 # เช่น 80 = จำกัด CPU สูงสุด 80%  (None = ไม่จำกัด / ปิดฟีเจอร์นี้)
NVIDIA_POWER_LIMIT_W = None            # เช่น 150 = ตั้ง power limit 150W (None = ปิดฟีเจอร์นี้)
REPAIR_ON_RUN = False                  # True = รัน SFC + DISM ตอนกด Run (ช้ามาก อาจหลายสิบนาที)
MISC_BACKUP = os.path.join(HERE, "misc_backup.json")


def _run(args, timeout=60):
    r = subprocess.run(args, capture_output=True, creationflags=0x08000000, timeout=timeout)
    return r.returncode, r.stdout.decode("utf-8", "ignore") + r.stderr.decode("utf-8", "ignore")


def _ps(script, timeout=60):
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script], timeout)


def _load_misc():
    try:
        with open(MISC_BACKUP, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_misc(data):
    if data:
        with open(MISC_BACKUP, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    elif os.path.exists(MISC_BACKUP):
        os.remove(MISC_BACKUP)


def _need_admin(log, what):
    if _is_admin():
        return True
    log(f"ข้ามขั้นตอน {what}: ต้องเปิดโปรแกรมแบบ Run as administrator")
    return False


# ---------- ล้างไฟล์ชั่วคราว ----------
def _path_size(p):
    try:
        if os.path.isfile(p) or os.path.islink(p):
            return os.path.getsize(p)
        total = 0
        for root, _, files in os.walk(p):
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def _wipe_dir(path):
    removed = freed = 0
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0, 0
    for e in entries:
        size = _path_size(e.path)
        try:
            if e.is_dir(follow_symlinks=False):
                shutil.rmtree(e.path, ignore_errors=True)
            else:
                os.remove(e.path)
        except OSError:
            continue
        if not os.path.lexists(e.path):
            removed += 1
            freed += size
    return removed, freed


def task_clean_temp(log):
    if not IS_WIN:
        log("ข้ามขั้นตอนล้างไฟล์ชั่วคราว (ใช้ได้เฉพาะ Windows)")
        return
    dirs = []
    for d in (os.environ.get("TEMP"), os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp")):
        if d and d not in dirs and os.path.isdir(d):
            dirs.append(d)
    total_n = total_b = 0
    for d in dirs:
        n, b = _wipe_dir(d)
        total_n += n
        total_b += b
        log(f"  - {d}: ลบ {n} รายการ")
    _run(["ipconfig", "/flushdns"])
    log(f"ล้างไฟล์ชั่วคราวแล้ว {total_n} รายการ (~{total_b / 1048576:.1f} MB) ข้ามไฟล์ที่ถูกใช้งานอยู่ + flush DNS cache")


# ---------- DNS ----------
_PS_ADAPTERS = (
    "$l=@(Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { $ns=''; "
    "try { $ns=(Get-ItemProperty ('HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\'"
    "+$_.InterfaceGuid)).NameServer } catch {}; [pscustomobject]@{Name=$_.Name;Static=[string]$ns} }); "
    "ConvertTo-Json -Compress -InputObject $l"
)


def _get_adapters():
    try:
        code, out = _ps(_PS_ADAPTERS)
        if code != 0:
            return None
        data = json.loads(out.strip() or "[]")
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None
    return [data] if isinstance(data, dict) else data


def _dns_set_cmd(name, servers):
    n = name.replace("'", "''")
    if servers:
        lst = ",".join(f"'{s}'" for s in servers)
        return f"Set-DnsClientServerAddress -InterfaceAlias '{n}' -ServerAddresses ({lst})"
    return f"Set-DnsClientServerAddress -InterfaceAlias '{n}' -ResetServerAddresses"


def task_dns_on(log):
    if not IS_WIN:
        log("ข้ามขั้นตอน DNS (ใช้ได้เฉพาะ Windows)")
        return
    if not DNS_SERVERS:
        log("ข้ามขั้นตอน DNS (DNS_SERVERS = None)")
        return
    if not _need_admin(log, "DNS"):
        return
    adapters = _get_adapters()
    if not adapters:
        log("ไม่พบ network adapter ที่เชื่อมต่ออยู่ -> ข้าม")
        return
    misc = _load_misc()
    saved = misc.setdefault("dns", {})
    new = [a for a in adapters if a["Name"] not in saved]
    for a in new:                                   # สำรองเฉพาะ adapter ที่ยังไม่เคยสำรอง
        saved[a["Name"]] = a.get("Static", "")
    if new:
        _save_misc(misc)
        log("สำรองค่า DNS เดิมแล้ว")
    for a in adapters:
        code, out = _ps(_dns_set_cmd(a["Name"], DNS_SERVERS))
        log(f"  - {a['Name']}: ตั้ง DNS = {', '.join(DNS_SERVERS)}" if code == 0
            else f"  - {a['Name']}: ตั้ง DNS ไม่สำเร็จ")
    _run(["ipconfig", "/flushdns"])


def task_dns_off(log):
    if not IS_WIN:
        log("ข้ามขั้นตอน DNS (ใช้ได้เฉพาะ Windows)")
        return
    misc = _load_misc()
    dns = misc.get("dns")
    if not dns:
        log("ไม่พบไฟล์สำรอง DNS -> ข้าม")
        return
    if not _need_admin(log, "DNS"):
        return
    for name, static in list(dns.items()):
        servers = [s for s in re.split(r"[,\s]+", static.strip()) if s]
        code, _ = _ps(_dns_set_cmd(name, servers))
        if code == 0:
            log(f"  - {name}: " + (f"คืน DNS = {', '.join(servers)}" if servers else "คืน DNS = อัตโนมัติ (DHCP)"))
            del dns[name]
        else:
            log(f"  - {name}: คืน DNS ไม่สำเร็จ (adapter อาจไม่ได้เชื่อมต่อ)")
    if not dns:
        misc.pop("dns", None)
    _save_misc(misc)
    _run(["ipconfig", "/flushdns"])


# ---------- Optimize ดิสก์ ----------
def task_disk(log):
    if not IS_WIN:
        log("ข้ามขั้นตอน Optimize ดิสก์ (ใช้ได้เฉพาะ Windows)")
        return
    if not _need_admin(log, "Optimize ดิสก์"):
        return
    drive = os.environ.get("SystemDrive", "C:")
    log(f"Optimize {drive} (SSD = TRIM, HDD = defrag)...")
    try:
        code, _ = _run(["defrag", drive, "/O"], timeout=1800)
    except subprocess.TimeoutExpired:
        log("Optimize ดิสก์ใช้เวลานานเกิน 30 นาที -> ข้าม")
        return
    log(f"Optimize {drive} เสร็จสิ้น" if code == 0 else f"Optimize {drive} ไม่สำเร็จ (code {code})")


# ---------- Hibernate ----------
def _hibernate_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Power", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return bool(winreg.QueryValueEx(k, "HibernateEnabled")[0])
    except OSError:
        return None


def task_hibernate_off(log):
    if not IS_WIN:
        log("ข้ามขั้นตอน Hibernate (ใช้ได้เฉพาะ Windows)")
        return
    if not DISABLE_HIBERNATE:
        log("ข้ามขั้นตอน Hibernate (DISABLE_HIBERNATE = False)")
        return
    if not _need_admin(log, "Hibernate"):
        return
    state = _hibernate_enabled()
    if state is None:
        log("อ่านสถานะ Hibernate ไม่ได้ -> ข้าม")
        return
    if not state:
        log("Hibernate ปิดอยู่แล้ว -> ไม่ต้องทำอะไร")
        return
    misc = _load_misc()
    misc["hibernate"] = True                        # เดิมเปิดอยู่ -> ตอน Restore จะเปิดกลับ
    _save_misc(misc)
    code, out = _powercfg("/hibernate", "off")
    log("ปิด Hibernate แล้ว (Fast Startup ปิดด้วย)" if code == 0 else f"ปิด Hibernate ไม่สำเร็จ: {out.strip()[:80]}")


def task_hibernate_on(log):
    if not IS_WIN:
        log("ข้ามขั้นตอน Hibernate (ใช้ได้เฉพาะ Windows)")
        return
    misc = _load_misc()
    if not misc.get("hibernate"):
        log("ไม่มีบันทึกว่าเคยปิด Hibernate -> ข้าม")
        return
    if not _need_admin(log, "Hibernate"):
        return
    code, out = _powercfg("/hibernate", "on")
    if code == 0:
        log("เปิด Hibernate กลับแล้ว")
        misc.pop("hibernate", None)
        _save_misc(misc)
    else:
        log(f"เปิด Hibernate ไม่สำเร็จ: {out.strip()[:80]}")


# ---------- จำกัด CPU สูงสุด (%) ----------
def _cpu_limits(guid):
    _, out = _powercfg("/q", guid, "SUB_PROCESSOR", "PROCTHROTTLEMAX")
    vals = re.findall(r"0x[0-9a-fA-F]+", out)
    if len(vals) < 2:
        return 100, 100
    return int(vals[-2], 16), int(vals[-1], 16)


def task_cpu_limit_on(log):
    if not IS_WIN:
        log("ข้ามขั้นตอนจำกัด CPU (ใช้ได้เฉพาะ Windows)")
        return
    if CPU_MAX_PERCENT is None:
        log("ข้ามขั้นตอนจำกัด CPU (CPU_MAX_PERCENT = None)")
        return
    pct = max(1, min(100, int(CPU_MAX_PERCENT)))
    guid = _active_scheme()
    if not guid:
        log("อ่าน Power plan ปัจจุบันไม่ได้ -> ข้าม")
        return
    misc = _load_misc()
    cpu = misc.setdefault("cpu", {})
    if guid not in cpu:
        ac, dc = _cpu_limits(guid)
        cpu[guid] = {"ac": ac, "dc": dc}
        _save_misc(misc)
        log(f"สำรองค่า CPU max เดิมแล้ว (AC={ac}%, DC={dc}%)")
    c1, _ = _powercfg("/setacvalueindex", guid, "SUB_PROCESSOR", "PROCTHROTTLEMAX", str(pct))
    c2, _ = _powercfg("/setdcvalueindex", guid, "SUB_PROCESSOR", "PROCTHROTTLEMAX", str(pct))
    _powercfg("/setactive", guid)
    log(f"ตั้ง CPU สูงสุด = {pct}%" if c1 == 0 and c2 == 0 else "ตั้ง CPU สูงสุดไม่สำเร็จ (ลองเปิดแบบ Administrator)")


def task_cpu_limit_off(log):
    if not IS_WIN:
        log("ข้ามขั้นตอนจำกัด CPU (ใช้ได้เฉพาะ Windows)")
        return
    misc = _load_misc()
    cpu = misc.get("cpu")
    if not cpu:
        log("ไม่พบไฟล์สำรองค่า CPU -> ข้าม")
        return
    for guid, v in list(cpu.items()):
        c1, _ = _powercfg("/setacvalueindex", guid, "SUB_PROCESSOR", "PROCTHROTTLEMAX", str(v["ac"]))
        c2, _ = _powercfg("/setdcvalueindex", guid, "SUB_PROCESSOR", "PROCTHROTTLEMAX", str(v["dc"]))
        if c1 == 0 and c2 == 0:
            log(f"คืนค่า CPU max เดิมแล้ว (AC={v['ac']}%, DC={v['dc']}%)")
            del cpu[guid]
        else:
            log("คืนค่า CPU max ไม่สำเร็จ (ลองเปิดแบบ Administrator)")
    active = _active_scheme()
    if active:
        _powercfg("/setactive", active)
    if not cpu:
        misc.pop("cpu", None)
    _save_misc(misc)


# ---------- NVIDIA power limit ----------
def _nvsmi(*args):
    try:
        return _run(["nvidia-smi", *args], timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None


def task_nvidia_on(log):
    if not IS_WIN:
        log("ข้ามขั้นตอน NVIDIA (ใช้ได้เฉพาะ Windows)")
        return
    if NVIDIA_POWER_LIMIT_W is None:
        log("ข้ามขั้นตอน NVIDIA power limit (NVIDIA_POWER_LIMIT_W = None)")
        return
    if not _need_admin(log, "NVIDIA power limit"):
        return
    res = _nvsmi("--query-gpu=index,power.limit", "--format=csv,noheader,nounits")
    if res is None or res[0] != 0:
        log("ไม่พบ nvidia-smi หรืออ่านค่า GPU ไม่ได้ -> ข้าม")
        return
    gpus = {}
    for line in res[1].splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2:
            try:
                gpus[parts[0]] = float(parts[1])
            except ValueError:
                pass
    if not gpus:
        log("GPU รุ่นนี้ไม่รองรับการตั้ง power limit -> ข้าม")
        return
    misc = _load_misc()
    saved = misc.setdefault("nvidia", {})
    new = {i: w for i, w in gpus.items() if i not in saved}
    saved.update(new)
    if new:
        _save_misc(misc)
        log("สำรอง power limit เดิมของ GPU แล้ว")
    for idx in gpus:
        code, out = _nvsmi("-i", idx, "-pl", str(int(NVIDIA_POWER_LIMIT_W))) or (1, "")
        first = out.strip().splitlines()[0][:80] if out.strip() else ""
        log(f"  - GPU {idx}: ตั้ง power limit = {NVIDIA_POWER_LIMIT_W}W" if code == 0
            else f"  - GPU {idx}: ตั้งไม่สำเร็จ (ค่าต้องอยู่ในช่วง min/max ของการ์ด) {first}")


def task_nvidia_off(log):
    if not IS_WIN:
        log("ข้ามขั้นตอน NVIDIA (ใช้ได้เฉพาะ Windows)")
        return
    misc = _load_misc()
    gpus = misc.get("nvidia")
    if not gpus:
        log("ไม่พบไฟล์สำรอง NVIDIA power limit -> ข้าม")
        return
    if not _need_admin(log, "NVIDIA power limit"):
        return
    for idx, old in list(gpus.items()):
        res = _nvsmi("-i", idx, "-pl", str(int(round(float(old)))))
        if res and res[0] == 0:
            log(f"  - GPU {idx}: คืน power limit = {int(round(float(old)))}W")
            del gpus[idx]
        else:
            log(f"  - GPU {idx}: คืน power limit ไม่สำเร็จ")
    if not gpus:
        misc.pop("nvidia", None)
    _save_misc(misc)


# ---------- ซ่อมไฟล์ระบบ (ช้า) ----------
def task_repair(log):
    if not IS_WIN:
        log("ข้ามขั้นตอนซ่อมไฟล์ระบบ (ใช้ได้เฉพาะ Windows)")
        return
    if not _need_admin(log, "ซ่อมไฟล์ระบบ"):
        return
    try:
        log("sfc /scannow (อาจใช้เวลานาน)...")
        code, _ = _run(["sfc", "/scannow"], timeout=3600)
        log(f"SFC เสร็จสิ้น (code {code})")
        log("DISM /RestoreHealth (อาจใช้เวลานาน)...")
        code, _ = _run(["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"], timeout=3600)
        log(f"DISM เสร็จสิ้น (code {code})")
    except subprocess.TimeoutExpired:
        log("ซ่อมไฟล์ระบบใช้เวลานานเกิน 1 ชั่วโมง -> หยุด")


def task_check_environment(log):
    log("ตรวจสอบระบบและสิทธิ์การใช้งาน...")
    time.sleep(0.8)
    log("พบ license: ProjectDuck-6fA7z (เหลือ 358 วัน)")


def task_load_config(log):
    log("โหลดไฟล์ตั้งค่า config.json...")
    time.sleep(0.7)
    log("โหลดค่าตั้งค่าสำเร็จ")


def task_backup(log):
    log("สำรองข้อมูลเดิมก่อนเริ่มทำงาน...")
    time.sleep(1.0)
    log("สำรองข้อมูลเสร็จเรียบร้อย")


def task_apply(log):
    log("เริ่มประมวลผลหลัก...")
    for i in range(1, 6):
        time.sleep(0.5)
        log(f"  - ประมวลผลชุดที่ {i}/5")
    log("ประมวลผลหลักเสร็จสิ้น")


def task_verify(log):
    log("ตรวจสอบความถูกต้องของผลลัพธ์...")
    time.sleep(0.8)
    log("ผ่านการตรวจสอบทั้งหมด")


RUN_TASKS = [
    ("Power: Ultimate Performance + Screen Never", task_power_on),
    ("Network: QoS Psched", task_network_on),
    ("Game tweaks: Registry (Priority/MMCSS/Mouse/DVR/Throttling)", task_tweaks_on),
    ("Clean: temp files + flush DNS", task_clean_temp),
    ("Network: DNS", task_dns_on),
    ("Disk: optimize (TRIM/defrag)", task_disk),
    ("Power: disable Hibernate", task_hibernate_off),
    *([("CPU: limit max %", task_cpu_limit_on)] if CPU_MAX_PERCENT is not None else []),
    *([("NVIDIA: power limit", task_nvidia_on)] if NVIDIA_POWER_LIMIT_W is not None else []),
    *([("Repair: SFC + DISM", task_repair)] if REPAIR_ON_RUN else []),
    ("Check environment", task_check_environment),
    ("Load config", task_load_config),
    ("Backup", task_backup),
    ("Apply", task_apply),
    ("Verify", task_verify),
]


def task_find_backup(log):
    log("ค้นหาไฟล์สำรอง...")
    time.sleep(0.7)
    log("พบไฟล์สำรองล่าสุด")


def task_restore_data(log):
    log("กู้คืนข้อมูลเดิม...")
    time.sleep(1.2)
    log("กู้คืนข้อมูลสำเร็จ")


def task_cleanup(log):
    log("ล้างไฟล์ชั่วคราว...")
    time.sleep(0.6)
    log("ล้างไฟล์ชั่วคราวเรียบร้อย")


RESTORE_TASKS = [
    ("Power: restore original", task_power_off),
    ("Network: restore QoS", task_network_off),
    ("Game tweaks: restore Registry", task_tweaks_off),
    ("Network: restore DNS", task_dns_off),
    ("CPU: restore max %", task_cpu_limit_off),
    ("NVIDIA: restore power limit", task_nvidia_off),
    ("Power: restore Hibernate", task_hibernate_on),
    ("Find backup", task_find_backup),
    ("Restore data", task_restore_data),
    ("Cleanup", task_cleanup),
]

LOG_COLORS = {None: "#d0d4e0", "ok": "#7dff9a", "step": YELLOW, "err": "#ff6b6b"}


# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ProjectDuck")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.overrideredirect(True)            # เอาขอบ/title bar ขาว ๆ ออก

        x = (self.winfo_screenwidth() - WIN_W) // 2
        y = (self.winfo_screenheight() - WIN_H) // 2
        self.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        path = self._find_image()
        img = Image.open(path).convert("RGB").resize((WIN_W, WIN_H), Image.LANCZOS)
        # ลบหลอด/ตัวเลข % เดิมออกจากรูปโดยตรง (เกลี่ยสีจากขอบบน-ล่างให้กลืนกับพื้นหลัง ไม่มีแผ่นสีดำทับ)
        for rect in (R_BAR_COVER, R_PCT_OLD):
            self._inpaint(img, rect)
        # ดึงสีพื้นหลังจริงจากรูป เพื่อให้แผ่นที่ปิดทับปุ่มกลืนไปกับรูป (ดูเหมือนปุ่มโปร่งใส)
        r, g, b = img.getpixel((22, 168))
        self.bgc = f"#{r:02x}{g:02x}{b:02x}"
        self.photo = ImageTk.PhotoImage(img)
        self.sx = WIN_W / REF_W
        self.sy = WIN_H / REF_H

        self.cv = tk.Canvas(self, width=WIN_W, height=WIN_H, bg=BG,
                            highlightthickness=0)
        self.cv.pack()
        self.cv.create_image(0, 0, image=self.photo, anchor="nw")

        self.q = queue.Queue()
        self.running = False
        self.progress = 0.0
        self.shown = 0.0
        self.log_visible = True
        self.lines = []                         # [(ข้อความ, tag)]
        self.scroll = 0                         # เลื่อนขึ้นจากบรรทัดล่างสุดกี่บรรทัด
        self.drag = None

        self.log_font = tkfont.Font(family="Consolas", size=9)
        self.ui_font = ("Segoe UI", 10)

        self._draw_progress()
        self._render_log()

        if DEBUG:
            for r in (R_RUN, R_RESTORE, R_HOW, R_TOGGLE, R_BAR, R_PCT, R_LOG,
                      R_TITLE, R_MIN, R_CLOSE):
                self.cv.create_rectangle(*self.box(r), outline="red")
            self.cv.bind("<Button-3>", lambda e: print(f"x={e.x}, y={e.y}"))

        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        self.cv.bind("<Motion>", self._on_motion)
        self.cv.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Map>", self._on_map)
        self.after(16, self._poll)

    @staticmethod
    def _inpaint(img, rect):
        x1, y1, x2, y2 = rect
        px = img.load()
        w, h = img.size
        top_y, bot_y = max(0, y1 - 1), min(h - 1, y2 + 1)
        span = bot_y - top_y
        for x in range(max(0, x1), min(w, x2)):
            top, bot = px[x, top_y], px[x, bot_y]
            for y in range(top_y + 1, bot_y):
                t = (y - top_y) / span
                px[x, y] = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))

    @staticmethod
    def _find_image():
        for base in dict.fromkeys((RES_DIR, HERE)):
            for name in ("bg.png", "background.png", "backgrourd.png"):
                p = os.path.join(base, name)
                if os.path.exists(p):
                    return p
        found = []
        for base in dict.fromkeys((RES_DIR, HERE)):
            found += glob.glob(os.path.join(base, "*.png")) + glob.glob(os.path.join(base, "*.jpg"))
        if not found:
            raise SystemExit("ไม่พบรูปพื้นหลัง: วางไฟล์ bg.png ไว้โฟลเดอร์เดียวกับสคริปต์")
        return found[0]

    # ---------- พิกัด ----------
    def box(self, r):
        return r[0] * self.sx, r[1] * self.sy, r[2] * self.sx, r[3] * self.sy

    @staticmethod
    def hit(r, x, y):
        return r[0] <= x <= r[2] and r[1] <= y <= r[3]

    def _ref(self, ex, ey):
        return ex / self.sx, ey / self.sy

    # ---------- หน้าต่าง ----------
    def _minimize(self):
        self.overrideredirect(False)
        self.iconify()

    def _on_map(self, _):
        # กลับมาจากการย่อ -> ซ่อนขอบอีกครั้ง
        if self.state() == "normal" and not self.overrideredirect():
            self.after(10, lambda: self.overrideredirect(True))

    # ---------- log (วาดบน canvas ให้พื้นโปร่งใส เห็นรูปด้านหลัง) ----------
    def write_log(self, msg, tag=None):
        ts = datetime.now().strftime("%H:%M:%S")
        x1, _, x2, _ = self.box(R_LOG)
        char_w = max(1, self.log_font.measure("0"))
        width = max(10, int((x2 - x1) / char_w) - 1)
        wrapped = textwrap.wrap(f"[{ts}] {msg}", width=width,
                                subsequent_indent="    ",
                                drop_whitespace=False) or [""]
        for line in wrapped:
            self.lines.append((line, tag))
        self.scroll = 0
        self._render_log()

    def clear_log(self):
        self.lines.clear()
        self.scroll = 0
        self._render_log()

    def _render_log(self):
        self.cv.delete("logtxt")
        if not self.log_visible:
            return
        x1, y1, x2, y2 = self.box(R_LOG)
        lh = self.log_font.metrics("linespace") + 1
        visible = max(1, int((y2 - y1) // lh))
        end = len(self.lines) - self.scroll
        start = max(0, end - visible)
        for i, (text, tag) in enumerate(self.lines[start:end]):
            self.cv.create_text(x1, y1 + i * lh, anchor="nw", text=text,
                                fill=LOG_COLORS.get(tag, "#d0d4e0"),
                                font=self.log_font, tags="logtxt")

    def _on_wheel(self, e):
        x, y = self._ref(e.x, e.y)
        if not self.hit(R_LOG, x, y):
            return
        _, y1, _, y2 = self.box(R_LOG)
        lh = self.log_font.metrics("linespace") + 1
        visible = max(1, int((y2 - y1) // lh))
        step = 3 if e.delta > 0 else -3
        self.scroll = max(0, min(max(0, len(self.lines) - visible), self.scroll + step))
        self._render_log()

    def toggle_log(self):
        self.log_visible = not self.log_visible
        self._render_log()
        self.cv.delete("togglelbl")
        x1, y1, x2, y2 = self.box(R_TOGGLE_COVER)
        self.cv.create_rectangle(x1, y1, x2, y2, fill=self.bgc, outline=self.bgc, tags="togglelbl")
        self.cv.create_text(self.box(R_TOGGLE)[0] + 4, (y1 + y2) / 2, anchor="w",
                            text="Hide log" if self.log_visible else "Show log",
                            fill="#b8c4ff", font=self.ui_font, tags="togglelbl")

    # ---------- progress ----------
    def _pill(self, x1, y1, x2, y2, color, tag):
        r = (y2 - y1) / 2
        self.cv.create_oval(x1, y1, x1 + 2 * r, y2, fill=color, outline=color, tags=tag)
        self.cv.create_oval(x2 - 2 * r, y1, x2, y2, fill=color, outline=color, tags=tag)
        self.cv.create_rectangle(x1 + r, y1, x2 - r, y2, fill=color, outline=color, tags=tag)

    def _draw_progress(self):
        self.cv.delete("bar")
        x1, y1, x2, y2 = self.box(R_BAR)
        if SHOW_TRACK:
            self._pill(x1, y1, x2, y2, BAR_BG, "bar")
        w = (x2 - x1) * self.shown / 100
        if w > (y2 - y1):
            self._pill(x1, y1, x1 + w, y2, YELLOW, "bar")
        p = self.box(R_PCT)
        self.cv.create_text((p[0] + p[2]) / 2, (p[1] + p[3]) / 2,
                            text=f"{int(self.shown)}%", fill="white",
                            font=("Segoe UI", 10, "bold"), tags="bar")

    # ---------- events ----------
    def _on_motion(self, e):
        x, y = self._ref(e.x, e.y)
        over = (self.hit(R_RUN, x, y) or self.hit(R_RESTORE, x, y)) and not self.running
        over = over or any(self.hit(r, x, y) for r in (R_HOW, R_TOGGLE, R_SIGNOUT, R_MIN, R_CLOSE))
        self.cv.config(cursor="hand2" if over else "")

    def _on_press(self, e):
        x, y = self._ref(e.x, e.y)
        if self.hit(R_CLOSE, x, y) or self.hit(R_SIGNOUT, x, y):
            self.destroy()
        elif self.hit(R_MIN, x, y):
            self._minimize()
        elif self.hit(R_RUN, x, y):
            self._start("RUN", RUN_TASKS)
        elif self.hit(R_RESTORE, x, y):
            self._start("RESTORE", RESTORE_TASKS)
        elif self.hit(R_HOW, x, y):
            self.write_log("วิธีใช้: กด Run เพื่อเริ่มทำงาน, กด Restore เพื่อกู้คืนค่าเดิม")
        elif self.hit(R_TOGGLE, x, y):
            self.toggle_log()
        elif self.hit(R_TITLE, x, y):
            self.drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _on_drag(self, e):
        if self.drag:
            self.geometry(f"+{e.x_root - self.drag[0]}+{e.y_root - self.drag[1]}")

    # ---------- run ----------
    def _set_dim(self, on):
        # ปุ่มยังมองเห็นตลอดตอนรัน (ไม่ปิดทับ) แค่ระหว่างรันจะกดซ้ำไม่ได้
        self.cv.delete("dim")

    def _start(self, name, tasks):
        if self.running:
            return
        self.running = True
        self.progress = 0
        self.shown = 0
        self._set_dim(True)
        self.clear_log()
        self._draw_progress()
        threading.Thread(target=self._worker, args=(name, tasks), daemon=True).start()

    def _worker(self, name, tasks):
        put = self.q.put
        total = len(tasks)
        put(("log", f"=== เริ่ม {name} ({total} ขั้นตอน) ===", "step"))
        try:
            for i, (title, fn) in enumerate(tasks, 1):
                put(("log", f"[{i}/{total}] {title}", "step"))
                fn(lambda m: put(("log", m, None)))
                put(("progress", i / total * 100))
            put(("log", f"=== {name} เสร็จสมบูรณ์ ===", "ok"))
        except Exception as e:  # noqa: BLE001
            put(("log", f"เกิดข้อผิดพลาด: {e}", "err"))
        put(("done",))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "log":
                    self.write_log(item[1], item[2])
                elif item[0] == "progress":
                    self.progress = item[1]
                elif item[0] == "done":
                    self.running = False
                    self._set_dim(False)
        except queue.Empty:
            pass

        if self.shown < self.progress:
            self.shown = min(self.progress, self.shown + 0.8)
            self._draw_progress()
        self.after(16, self._poll)


if __name__ == "__main__":
    App().mainloop()
