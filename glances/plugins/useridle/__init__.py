# useridle last mod: 04/11/2025 22h45 Claud's solution
# This file is part of Glances.
#
# written by Pete BOS and friends (Gemini and Grok)
#
# intended to detect user inactivity of personal computers in order to switch them off/ make them go to sleep. (With Home Assistant)
# has been tested with Windows and Linux (Debian with xTerm) requires xprintfile sudo apt install xprintidle

import ctypes
import os
import platform
import subprocess
import shutil
import sys
from datetime import datetime, timedelta
import time

# Glances imports
from glances.plugins.plugin.model import GlancesPluginModel
from glances.logger import logger


# ----------------------------------------------------------------------
# Windows – user-idle detection (file-based, works from service)
# ----------------------------------------------------------------------
if sys.platform.startswith('win'):
    import os
    import ctypes
    from ctypes import wintypes

    # ------------------------------------------------------------------
    # 1. Path to the marker file
    # ------------------------------------------------------------------
    IDLE_FILE = r"C:\Windows\Temp\CurrentIdleTime.txt"

    # ------------------------------------------------------------------
    # 2. Helper – seconds since the system booted
    # ------------------------------------------------------------------
    _GetTickCount64 = getattr(ctypes.windll.kernel32, 'GetTickCount64', None)
    _GetTickCount   = ctypes.windll.kernel32.GetTickCount

    def _uptime_seconds() -> float:
        """Return system uptime in seconds (float)."""
        tick = _GetTickCount64() if _GetTickCount64 else _GetTickCount()
        return tick / 1000.0

    # ------------------------------------------------------------------
    # 3. Main idle-time function
    # ------------------------------------------------------------------
    def _get_windows_idle_time() -> float:
        """
        Return user-idle seconds.

        * If the marker file exists → read the integer inside it.
        * Otherwise → return system uptime.
        """
        # 1. Try to read the file
        if os.path.isfile(IDLE_FILE):
            try:
                with open(IDLE_FILE, 'r', encoding='ascii') as f:
                    content = f.read().strip()
                # The file contains only a decimal number (no newline)
                idle_sec = int(content)
                logger.debug(f"useridle: file {IDLE_FILE!r} → {idle_sec}s")
                return float(idle_sec)
            except Exception as exc:
                logger.debug(f"useridle: failed to read {IDLE_FILE!r}: {exc}")

        # 2. No file → no logged-in user → report uptime
        uptime = _uptime_seconds()
        logger.debug(f"useridle: no file → uptime {uptime:.1f}s")
        return uptime
    # ----------------------------------------------------------------------
# Linux – unchanged original implementation
# ----------------------------------------------------------------------
elif sys.platform.startswith('linux'):
    _XPRINTIDLE_AVAILABLE = False
    _XPRINTIDLE_CHECKED = False
    _BOOT_TIME = datetime.now() - timedelta(seconds=time.clock_gettime(time.CLOCK_BOOTTIME))

    def _check_xprintidle_availability():
        """Checks and caches if xprintidle is installed."""
        global _XPRINTIDLE_AVAILABLE, _XPRINTIDLE_CHECKED
        if not _XPRINTIDLE_CHECKED:
            _XPRINTIDLE_AVAILABLE = shutil.which('xprintidle') is not None
            if not _XPRINTIDLE_AVAILABLE:
                logger.warning(
                    "useridle: xprintidle command not found. Please install it (e.g., 'sudo apt install xprintidle').")
            _XPRINTIDLE_CHECKED = True
        return _XPRINTIDLE_AVAILABLE

    def _get_linux_idle_time():
        """
        Gets idle time. Returns time since boot if no valid Xorg session is detected,
        otherwise uses xprintidle.
        """
        # ---- No DISPLAY -------------------------------------------------
        if 'DISPLAY' not in os.environ or not os.environ['DISPLAY']:
            idle_seconds = (datetime.now() - _BOOT_TIME).total_seconds()
            logger.debug(f"useridle: No DISPLAY – reporting time since boot: {int(idle_seconds)}s")
            return idle_seconds

        # ---- xprintidle not installed -----------------------------------
        if not _check_xprintidle_availability():
            idle_seconds = (datetime.now() - _BOOT_TIME).total_seconds()
            logger.debug(f"useridle: xprintidle missing – reporting time since boot: {int(idle_seconds)}s")
            return idle_seconds

        # ---- Verify that the DISPLAY is actually usable -----------------
        try:
            subprocess.run(['xset', 'q'], capture_output=True, text=True,
                           timeout=2, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            idle_seconds = (datetime.now() - _BOOT_TIME).total_seconds()
            logger.debug(f"useridle: DISPLAY invalid – reporting time since boot: {int(idle_seconds)}s")
            return idle_seconds

        # ---- Run xprintidle ---------------------------------------------
        try:
            result = subprocess.run(['xprintidle'], capture_output=True,
                                    text=True, timeout=5, check=True)
            idle_ms = int(result.stdout.strip())
            logger.debug(f"useridle: xprintidle reports {idle_ms/1000.0}s")
            return idle_ms / 1000.0
        except Exception as e:
            logger.debug(f"useridle: xprintidle failed ({e}) – falling back to boot time")
            return (datetime.now() - _BOOT_TIME).total_seconds()


# ----------------------------------------------------------------------
# Fallback for unsupported OSes
# ----------------------------------------------------------------------
else:
    _get_windows_idle_time = None
    _get_linux_idle_time = None


# ----------------------------------------------------------------------
# Glances Plugin
# ----------------------------------------------------------------------
class UseridlePlugin(GlancesPluginModel):
    """Glances plugin to display user idle time."""
    def __init__(self, args=None, config=None):
        super().__init__(args=args, config=config)
        logger.debug("useridle: Plugin loaded.")

        self.display_curse = True
        self.align = 'right'

        self.platform = sys.platform
        self.idle_seconds = 0.0
        self.idle_timedelta = timedelta(seconds=0)

        # ---- Platform capability check ----------------------------------
        if self.platform.startswith('win'):
            self.disabled_msg = None
        elif self.platform.startswith('linux'):
            self.disabled_msg = None
        else:
            self.disabled_msg = "Unsupported OS."
            self.set_disabled()

        if self.is_disabled():
            logger.info(f"useridle plugin: Disabled ({self.disabled_msg}).")
        else:
            logger.info(f"useridle plugin: Initialized for {self.platform}.")

        # Thresholds (optional – Glances will use defaults if not set)
        self.careful_threshold = self.get_limit('careful')
        self.warning_threshold = self.get_limit('warning')
        self.critical_threshold = self.get_limit('critical')

    # ------------------------------------------------------------------
    def get_export(self):
        """Export idle time in seconds."""
        if self.is_disabled():
            return {'seconds': -1, 'status': 'disabled', 'reason': self.disabled_msg}
        return {'seconds': int(self.idle_seconds), 'status': self.get_stats()}

    # ------------------------------------------------------------------
    @GlancesPluginModel._check_decorator
    @GlancesPluginModel._log_result_decorator
    def update(self):
        """Update the plugin data."""
        if self.is_disabled():
            self.stats = self.disabled_msg or "N/A"
            return self.stats

        idle_time_s = None

        if self.platform.startswith('win'):
            idle_time_s = _get_windows_idle_time()
        elif self.platform.startswith('linux'):
            idle_time_s = _get_linux_idle_time()

        # ---- Process a valid number ------------------------------------
        if idle_time_s is not None:
            self.idle_seconds = idle_time_s
            self.idle_timedelta = timedelta(seconds=int(self.idle_seconds))

            total_seconds = int(self.idle_seconds)
            days = total_seconds // (24 * 3600)
            rem = total_seconds % (24 * 3600)
            hours = rem // 3600
            rem %= 3600
            minutes = rem // 60
            seconds = rem % 60

            if days:
                self.stats = f"{days}d {hours:02}:{minutes:02}:{seconds:02}"
            else:
                self.stats = f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            # ---- Error path ------------------------------------------------
            self.idle_seconds = 0.0
            self.idle_timedelta = timedelta(seconds=0)
            self.stats = "N/A"
            logger.error("useridle: Could not retrieve idle time – displaying 'N/A'.")

        return self.stats

    # ------------------------------------------------------------------
    def msg_curse(self, args=None, max_width=None):
        """Curses UI line."""
        if self.is_disabled() or self.stats == "N/A":
            return []                               # nothing to display
        return [self.curse_add_line(f"UI IDLE: {self.stats}")]

    # ------------------------------------------------------------------
    def get_name(self):
        return "useridle"