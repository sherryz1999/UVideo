#!/usr/bin/env python3
"""
YouTube player with automatic ad skipping via browser automation.

Requirements:
    pip install selenium webdriver-manager
    Chrome browser must be installed.
"""

import sys
import time
import random
import argparse
import json
import os
import re
import shutil
import sqlite3
import base64
import ctypes
import ctypes.wintypes
import tempfile
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    NoSuchElementException,
    WebDriverException,
    InvalidSessionIdException,
)

try:
    import undetected_chromedriver as uc
    USE_UC = True
except ImportError:
    USE_UC = False
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    try:
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        USE_WEBDRIVER_MANAGER = True
    except ImportError:
        USE_WEBDRIVER_MANAGER = False

# Skip button selectors (YouTube occasionally changes class names)
AD_SKIP_SELECTORS = [
    ".ytp-skip-ad-button",
    ".ytp-ad-skip-button",
    ".ytp-ad-skip-button-modern",
    ".ytp-ad-skip-button-slot button",
    "button[class*='skip-ad']",
]

# Selectors that confirm an ad is currently playing
AD_ACTIVE_SELECTORS = [
    ".ytp-ad-simple-ad-badge",
    ".ytp-ad-text",
    ".ytp-ad-preview-text",
    ".ytp-ad-preview-container",
    ".ytp-ad-player-overlay",
]

AD_COUNTDOWN_SELECTOR = ".ytp-ad-duration-remaining"

COOKIE_ACCEPT_XPATHS = [
    '//button[@aria-label="Accept all"]',
    '//button[normalize-space()="Accept all"]',
    '//button[normalize-space()="Agree"]',
]


def build_driver():
    _CHROME_FLAGS = [
        "--start-maximized",
        "--disable-notifications",
        "--autoplay-policy=no-user-gesture-required",
        # Chrome 127+ locks the cookie DB by default; disable so yt-dlp can read it.
        "--disable-features=LockProfileCookieDatabase",
    ]
    if USE_UC:
        options = uc.ChromeOptions()
        for f in _CHROME_FLAGS:
            options.add_argument(f)
        driver = uc.Chrome(options=options, version_main=147)
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return driver

    # Fallback: plain selenium with anti-detection flags
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    for f in _CHROME_FLAGS:
        options.add_argument(f)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    if USE_WEBDRIVER_MANAGER:
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


_BROWSER_CLOSED_HINTS = (
    "invalid session id",
    "disconnected",
    "no such window",
    "target window already closed",
)


def _browser_was_closed(exc: WebDriverException) -> bool:
    msg = str(exc).lower()
    return any(hint in msg for hint in _BROWSER_CLOSED_HINTS)


def try_click(driver, selectors: list) -> bool:
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el.is_displayed() and el.is_enabled():
                time.sleep(random.uniform(0.3, 0.8))  # human-like pause before clicking
                el.click()
                return True
        except NoSuchElementException:
            pass
    return False


def ad_is_active(driver) -> bool:
    for sel in AD_ACTIVE_SELECTORS:
        try:
            if driver.find_element(By.CSS_SELECTOR, sel).is_displayed():
                return True
        except NoSuchElementException:
            pass
    return False


def ad_countdown(driver) -> str:
    try:
        el = driver.find_element(By.CSS_SELECTOR, AD_COUNTDOWN_SELECTOR)
        return el.text.strip()
    except NoSuchElementException:
        return ""


def dismiss_consent(driver) -> None:
    for xpath in COOKIE_ACCEPT_XPATHS:
        try:
            driver.find_element(By.XPATH, xpath).click()
            time.sleep(1)
            return
        except NoSuchElementException:
            pass


def ensure_playing(driver) -> None:
    try:
        btn = driver.find_element(By.CSS_SELECTOR, ".ytp-play-button")
        label = btn.get_attribute("aria-label") or ""
        if "Play" in label:
            btn.click()
    except NoSuchElementException:
        pass


def _write_status(path: str, status: str, message: str = "", progress: float = -1.0) -> None:
    if not path:
        return
    try:
        data: dict = {"status": status, "message": message}
        if progress >= 0:
            data["progress"] = progress
        with open(path, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def watch(url: str, poll_sec: float = 0.5, status_file: str = "") -> None:
    def ws(status, message=""):
        _write_status(status_file, status, message)

    print(f"Opening: {url}")
    ws("loading")
    driver = build_driver()

    try:
        driver.get(url)
        time.sleep(3)

        dismiss_consent(driver)
        ensure_playing(driver)

        print("Playing — monitoring for ads. Press Ctrl+C to quit.\n")
        ws("playing")
        in_ad = False

        while True:
            time.sleep(poll_sec)

            if try_click(driver, AD_SKIP_SELECTORS):
                print("\nAd skipped!")
                ws("skipped")
                in_ad = False
                time.sleep(0.6)
                ws("playing")
                continue

            if ad_is_active(driver):
                remaining = ad_countdown(driver)
                ws("ad", remaining)
                msg = f"Ad playing — {remaining} remaining..." if remaining else "Ad playing..."
                print(f"\r{msg:<50}", end="", flush=True)
                in_ad = True
            elif in_ad:
                print("\nAd finished — resuming video.")
                ws("playing")
                in_ad = False

    except KeyboardInterrupt:
        print("\n\nStopped.")
    except (InvalidSessionIdException, WebDriverException) as exc:
        if _browser_was_closed(exc):
            print("\nBrowser closed — session ended.")
        else:
            msg = exc.msg if hasattr(exc, "msg") else str(exc)
            print(f"\nBrowser error: {msg}")
            ws("error", msg)
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        ws("idle")


def _chrome_user_data() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")


def _json_cookies_to_netscape(json_path: str, output_path: str) -> bool:
    """Convert a Cookie-Editor JSON export to Netscape cookies.txt for yt-dlp."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        with open(output_path, "w", encoding="utf-8") as out:
            out.write("# Netscape HTTP Cookie File\n")
            for c in cookies:
                domain  = c.get("domain", "")
                subdoms = "TRUE" if domain.startswith(".") else "FALSE"
                path    = c.get("path", "/")
                secure  = "TRUE" if c.get("secure", False) else "FALSE"
                expires = int(c.get("expirationDate", 0)) if not c.get("session", True) else 0
                name    = c.get("name", "")
                value   = c.get("value", "")
                out.write(f"{domain}\t{subdoms}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
        return True
    except Exception as exc:
        print(f"Cookie JSON conversion failed: {exc}")
        return False


def _find_cookies_file() -> tuple:
    """
    Return (path, fmt) where fmt is 'netscape' or 'json', or ('', '').
    Netscape .txt files are used directly; JSON files are converted.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ("www.youtube.com_cookies.txt", "youtube_cookies.txt", "cookies.txt"):
        p = os.path.join(script_dir, name)
        if os.path.exists(p):
            return p, "netscape"
    for name in ("www.youtube.com_cookies.json", "youtube_cookies.json", "cookies.json"):
        p = os.path.join(script_dir, name)
        if os.path.exists(p):
            return p, "json"
    return "", ""


def _find_chrome_exe() -> str:
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", ""),       "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""),  "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),       "Google", "Chrome", "Application", "chrome.exe"),
        r"C:\Program Files\Chrome\chrome.exe",
    ]
    return next((p for p in candidates if p and os.path.exists(p)), "")


def _launch_unlocked_chrome(profile: str = "Default"):
    """
    Spawn a headless Chrome with --disable-features=LockProfileCookieDatabase so
    yt-dlp can read the cookie DB while Chrome is running.  Returns the Popen
    object (caller must terminate it) or None if Chrome wasn't found.
    """
    exe = _find_chrome_exe()
    if not exe:
        return None
    import subprocess as _sp
    try:
        return _sp.Popen(
            [
                exe,
                "--headless=new",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={_chrome_user_data()}",
                f"--profile-directory={profile}",
                "--disable-features=LockProfileCookieDatabase",
            ],
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
        )
    except Exception:
        return None


def list_chrome_profiles() -> list:
    """Return [{dir, name}, ...] for every Chrome profile that has a cookie DB."""
    base = _chrome_user_data()
    results = []
    if not os.path.isdir(base):
        return results
    for entry in os.listdir(base):
        if entry != "Default" and not entry.startswith("Profile "):
            continue
        has_cookies = (
            os.path.exists(os.path.join(base, entry, "Network", "Cookies")) or
            os.path.exists(os.path.join(base, entry, "Cookies"))
        )
        if not has_cookies:
            continue
        pref = os.path.join(base, entry, "Preferences")
        try:
            with open(pref, "r", encoding="utf-8") as f:
                name = json.load(f).get("profile", {}).get("name", entry)
        except Exception:
            name = entry
        results.append({"dir": entry, "name": name})
    # Default first, then Profile 1, Profile 2, …
    results.sort(key=lambda p: (0 if p["dir"] == "Default" else 1, p["dir"]))
    return results


def _extract_chrome_cookies(output_path: str, profile: str = "Default") -> tuple:
    """
    Read Chrome cookies from the locked SQLite DB via immutable mode,
    decrypt with Windows DPAPI + AES-128-GCM, write Netscape format.
    Returns (success: bool, error: str).
    Works while Chrome is running.  Does NOT handle Chrome 127+ v20 cookies
    (App-Bound Encryption); caller should fall back to --cookies-from-browser.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        local_state_path = os.path.join(_chrome_user_data(), "Local State")
        with open(local_state_path, "r", encoding="utf-8") as f:
            enc_key_b64 = json.load(f)["os_crypt"]["encrypted_key"]

        enc_key = base64.b64decode(enc_key_b64)[5:]  # strip "DPAPI" prefix

        class _BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf      = ctypes.create_string_buffer(enc_key, len(enc_key))
        blob_in  = _BLOB(ctypes.sizeof(buf), buf)
        blob_out = _BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out),
        )
        if not ok:
            return False, "Windows DPAPI decryption failed"
        aes_key = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)

        # Locate cookie DB for the requested profile
        profile_dir = os.path.join(_chrome_user_data(), profile)
        db_path = os.path.join(profile_dir, "Network", "Cookies")
        if not os.path.exists(db_path):
            db_path = os.path.join(profile_dir, "Cookies")
        if not os.path.exists(db_path):
            return False, f"Cookie DB not found for profile '{profile}'"

        import shutil
        tmp_db = tempfile.mktemp(suffix=".sqlite")
        try:
            shutil.copy2(db_path, tmp_db)
        except Exception as e:
            return False, f"Cannot copy cookie DB: {e}"
        # Copy WAL sidecar so uncommitted transactions are visible
        for ext in ("-wal", "-shm"):
            src = db_path + ext
            if os.path.exists(src):
                try:
                    shutil.copy2(src, tmp_db + ext)
                except Exception:
                    pass

        try:
            conn = sqlite3.connect(tmp_db)
            cur  = conn.cursor()
            cur.execute(
                "SELECT host_key, path, is_secure, expires_utc, name, encrypted_value "
                "FROM cookies "
                "WHERE host_key LIKE '%youtube%' OR host_key LIKE '%google%'"
            )
            rows = cur.fetchall()
            conn.close()
        finally:
            for ext in ("", "-wal", "-shm"):
                try:
                    os.remove(tmp_db + ext)
                except OSError:
                    pass

        # Chrome 127+ uses App-Bound Encryption (v20 prefix) which requires a
        # SYSTEM-level service to decrypt — fall back to --cookies-from-browser.
        v10 = sum(1 for *_, ev in rows if ev[:3] == b"v10")
        v20 = sum(1 for *_, ev in rows if ev[:3] == b"v20")
        if v20 > 0 and v10 == 0:
            return False, "app-bound-v20"

        with open(output_path, "w", encoding="utf-8") as out:
            out.write("# Netscape HTTP Cookie File\n")
            for host, path, secure, expires_utc, name, enc_val in rows:
                try:
                    if enc_val[:3] == b"v10":
                        nonce, ct = enc_val[3:15], enc_val[15:]
                        value = AESGCM(aes_key).decrypt(nonce, ct, None).decode()
                    else:
                        value = enc_val.decode("utf-8", errors="replace")
                except Exception:
                    value = ""
                unix_exp = max(0, (expires_utc - 11_644_473_600_000_000) // 1_000_000) if expires_utc else 0
                out.write(f"{host}\tTRUE\t{path}\t{'TRUE' if secure else 'FALSE'}\t{unix_exp}\t{name}\t{value}\n")

        return True, ""

    except Exception as exc:
        return False, str(exc)


def record_clip(
    url: str,
    start: str,
    end: str,
    output_path: str,
    status_file: str = "",
    chrome_profile: str = "Default",
) -> None:
    import subprocess
    import os

    def ws(s, m="", p=-1.0):
        _write_status(status_file, s, m, p)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    ws("recording", f"{start} → {end}")

    import datetime
    _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "record.log")
    _log_file = open(_log_path, "w", encoding="utf-8")

    def log(msg):
        line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        _log_file.write(line + "\n")
        _log_file.flush()

    log(f"Recording {start} → {end}  →  {output_path}")

    # Locate ffmpeg
    ffmpeg_exe = ""
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    ffmpeg_bin = ffmpeg_exe or "ffmpeg"

    # Locate Node.js / Deno for yt-dlp nsig decryption
    def _find_runtime(names, extra_paths=()):
        for name in names:
            p = shutil.which(name)
            if p:
                return p
        for p in extra_paths:
            p = os.path.expandvars(p)
            if os.path.isfile(p):
                return p
        return None

    _node_exe = _find_runtime(
        ["node"],
        [r"%ProgramFiles%\nodejs\node.exe",
         r"%ProgramFiles(x86)%\nodejs\node.exe",
         r"%LOCALAPPDATA%\Programs\nodejs\node.exe",
         r"%APPDATA%\npm\node.exe"],
    )
    _deno_exe = _find_runtime(["deno"], [r"%USERPROFILE%\.deno\bin\deno.exe"])
    _has_js = bool(_node_exe or _deno_exe)

    # yt-dlp runtime names: "node", "deno", "bun", "quickjs" (NOT "nodejs")
    if _node_exe:
        log(f"Node.js: {_node_exe}")
        js_args = ["--js-runtimes", f"node:{_node_exe}"]
    elif _deno_exe:
        log(f"Deno: {_deno_exe}")
        js_args = ["--js-runtimes", f"deno:{_deno_exe}"]
    else:
        log("WARNING: No JS runtime found")
        js_args = ["--js-runtimes", "node,deno"]

    def _secs(t):
        parts = t.strip().split(":")
        try:
            return sum(float(p) * 60**i for i, p in enumerate(reversed(parts)))
        except Exception:
            return 0.0

    _clip_secs = max(_secs(end) - _secs(start), 1.0)
    _FFMPEG_TIME_RE = re.compile(r'time=(\d+:\d+:\d+\.\d+)')
    _res_info = [""]

    AUTH_HINTS = ("sign in", "login", "age-restrict", "members only",
                  "not available", "unavailable", "private video", "country")

    # Find cookies file (Netscape .txt used directly; JSON converted)
    _netscape_cookies = ""
    _cookies_src, _cookies_fmt = _find_cookies_file()
    log(f"Cookies file: {_cookies_src or '(none)'} ({_cookies_fmt or '-'})")
    if _cookies_src:
        if _cookies_fmt == "netscape":
            _netscape_cookies = _cookies_src  # use directly, no conversion needed
        else:
            _netscape_cookies = tempfile.mktemp(suffix=".txt")
            if not _json_cookies_to_netscape(_cookies_src, _netscape_cookies):
                _netscape_cookies = ""

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # Add Node.js directory to PATH so yt-dlp subprocess can find it too
    if _node_exe:
        node_dir = os.path.dirname(_node_exe)
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")

    def _fetch_info():
        cmd = [
            "yt-dlp",
            "--cookies", _netscape_cookies,
            "--js-runtimes", f"node:{_node_exe}",
            "--remote-components", "ejs:github",
            "--no-playlist",
            "--dump-json",
            "-f", "bv*+ba/b",
            url,
        ]
        log("yt-dlp cmd: " + subprocess.list2cmdline(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        log(f"yt-dlp returncode: {r.returncode}")
        log("yt-dlp stderr: " + r.stderr[:1000])

        # log format summary from JSON (not the full JSON)
        for jline in reversed(r.stdout.strip().splitlines()):
            try:
                d = json.loads(jline)
                fmts = d.get("formats", [])
                log(f"formats in JSON: {len(fmts)}")
                for f in fmts:
                    log(f"  id={f.get('format_id')} height={f.get('height')} "
                        f"vcodec={f.get('vcodec')} acodec={f.get('acodec')} "
                        f"tbr={f.get('tbr')} proto={f.get('protocol')}")
                break
            except json.JSONDecodeError:
                continue
        return r

    def _pick_streams(info):
        """Return (urls, label, is_hls). Prefers DASH adaptive > HLS HD > combined."""
        fmts = info.get("formats", [])
        def has_v(f): return f.get("vcodec") not in (None, "none", "")
        def has_a(f): return f.get("acodec") not in (None, "none", "")
        def is_direct(f): return f.get("protocol", "") in ("https", "http", "http_dash_segments")
        def is_hls(f):    return "m3u8" in f.get("protocol", "")

        direct_fmts = [f for f in fmts if is_direct(f)]
        hls_fmts    = [f for f in fmts if is_hls(f) and has_v(f)]

        # Best case: DASH adaptive (separate video-only + audio-only streams)
        v_only = [f for f in direct_fmts if has_v(f) and not has_a(f)]
        a_only = [f for f in direct_fmts if has_a(f) and not has_v(f)]
        if v_only and a_only:
            bv = max(v_only, key=lambda f: (f.get("height",0), f.get("fps",0), f.get("tbr",0) or 0))
            ba = max(a_only, key=lambda f: f.get("abr",0) or f.get("tbr",0) or 0)
            return [bv["url"], ba["url"]], f"{bv.get('height','?')}p {bv.get('vcodec','?')} + audio", False

        # Combined DASH exists — but only use it if no HLS offers higher resolution
        combined = [f for f in direct_fmts if has_v(f) and has_a(f)]
        best_combined_h = max((f.get("height", 0) for f in combined), default=0)
        best_hls_h      = max((f.get("height", 0) for f in hls_fmts), default=0)

        if best_hls_h > best_combined_h:
            # HLS has better resolution; let yt-dlp handle download
            return [], f"{best_hls_h}p HLS", True

        if combined:
            bc = max(combined, key=lambda f: (f.get("height",0), f.get("tbr",0) or 0))
            return [bc["url"]], f"{bc.get('height','?')}p {bc.get('vcodec','?')} combined", False

        if hls_fmts:
            return [], f"{best_hls_h}p HLS", True

        if info.get("url"):
            return [info["url"]], "fallback", False
        return [], "no formats", False

    try:
        log("Fetching format info...")
        r = _fetch_info()

        if r.returncode != 0:
            lines = (r.stdout + r.stderr).strip().splitlines()
            err_lines = [l for l in lines if "ERROR:" in l]
            msg = err_lines[-1] if err_lines else (lines[-1] if lines else "yt-dlp failed")
            ws("error", msg)
            return

        # Parse the JSON — yt-dlp may emit warnings before the JSON line
        info = None
        for line in reversed(r.stdout.strip().splitlines()):
            try:
                info = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        if info is None:
            ws("error", "Could not parse yt-dlp output")
            return

        stream_urls, quality, use_hls = _pick_streams(info)
        log(f"Selected: {quality}  ({'HLS' if use_hls else 'DASH/direct'})")
        _res_info[0] = quality

        if not stream_urls and not use_hls:
            ws("error", "No downloadable streams found")
            return

        if use_hls:
            # HLS segments 403 when ffmpeg fetches without auth; let yt-dlp download
            yt_dl_cmd = [
                "yt-dlp",
                "--cookies", _netscape_cookies,
                "--js-runtimes", f"node:{_node_exe}",
                "--remote-components", "ejs:github",
                "--no-playlist",
                "-f", "bv*+ba/b",
                "--download-sections", f"*{_secs(start):.3f}-{_secs(end):.3f}",
                "--merge-output-format", "mp4",
                "-o", output_path,
                url,
            ]
            log("yt-dlp HLS cmd: " + subprocess.list2cmdline(yt_dl_cmd))
            _YT_PCT_RE = re.compile(r'(\d+\.?\d*)%')
            proc = subprocess.Popen(yt_dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1, env=env)
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                log(line)
                m = _YT_PCT_RE.search(line)
                if m:
                    pct = min(float(m.group(1)), 99.0)
                    ws("recording", f"{pct:.1f}%", pct)
            proc.wait()
        else:
            # DASH/direct: use ffmpeg with stream URLs
            # libx264 re-encode ensures clean cuts at non-keyframe boundaries
            ff = [ffmpeg_bin, "-y"]
            if len(stream_urls) >= 2:
                ff += ["-ss", start, "-to", end, "-i", stream_urls[0],
                       "-ss", start, "-to", end, "-i", stream_urls[1],
                       "-map", "0:v:0", "-map", "1:a:0",
                       "-c:v", "libx264", "-preset", "fast", "-c:a", "aac"]
            else:
                ff += ["-ss", start, "-to", end, "-i", stream_urls[0],
                       "-c:v", "libx264", "-preset", "fast", "-c:a", "aac"]
            ff.append(output_path)
            log("ffmpeg cmd: " + subprocess.list2cmdline(ff))
            proc = subprocess.Popen(ff, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1, env=env)
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                log(line)
                m = _FFMPEG_TIME_RE.search(line)
                if m:
                    pct = min(_secs(m.group(1)) / _clip_secs * 100, 99.0)
                    ws("recording", f"{pct:.1f}%", pct)
            proc.wait()

        if proc.returncode == 0:
            name = os.path.basename(output_path)
            ws("done", f"{name} | {_res_info[0]}")
        else:
            ws("error", "ffmpeg clip extraction failed")

    except FileNotFoundError as exc:
        msg = f"{'yt-dlp' if 'yt-dlp' in str(exc) else 'ffmpeg'} not found — run: pip install yt-dlp"
        log(msg)
        ws("error", msg)
    except Exception as exc:
        import traceback
        log(traceback.format_exc())
        ws("error", f"Error: {exc}")
    finally:
        if _netscape_cookies and _cookies_fmt == "json":
            try:
                os.remove(_netscape_cookies)
            except OSError:
                pass
        _log_file.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Play a YouTube video URL with automatic ad skipping."
    )
    parser.add_argument("url", nargs="?", help="YouTube video URL")
    parser.add_argument("--status-file", default="", dest="status_file",
                        help="Path for JSON status updates (used by GUI)")
    parser.add_argument("--record", action="store_true",
                        help="Record a clip instead of playing")
    parser.add_argument("--start-time", default="0:00", dest="start_time",
                        help="Clip start (e.g. 1:30 or 0:01:30)")
    parser.add_argument("--end-time", default="", dest="end_time",
                        help="Clip end (e.g. 2:00 or 0:02:00)")
    parser.add_argument("--output", default="clip.mp4",
                        help="Output file path for recorded clip")
    parser.add_argument("--chrome-profile", default="Default", dest="chrome_profile",
                        help="Chrome profile directory name (e.g. 'Default', 'Profile 1')")
    parser.add_argument("--list-profiles", action="store_true", dest="list_profiles",
                        help="Print available Chrome profiles as JSON and exit")
    args = parser.parse_args()

    if args.list_profiles:
        print(json.dumps(list_chrome_profiles()))
        sys.exit(0)

    url = (args.url or input("Enter YouTube URL: ")).strip()
    if not url:
        print("No URL provided.")
        sys.exit(1)

    if args.record:
        if not args.end_time:
            print("--end-time is required for recording.")
            sys.exit(1)
        record_clip(url, args.start_time, args.end_time, args.output,
                    args.status_file, args.chrome_profile)
    else:
        watch(url, status_file=args.status_file)


if __name__ == "__main__":
    main()
