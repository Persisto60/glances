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
# Windows – real user-idle detection (works when Glances runs as a service)
# ----------------------------------------------------------------------
if sys.platform.startswith('win'):
    import ctypes
    from ctypes import wintypes

    # ---- Windows constants ------------------------------------------------
    WTS_CURRENT_SERVER_HANDLE = 0
    DESKTOP_SWITCHDESKTOP     = 0x0100
    WTS_CONNECTSTATE_CLASS    = 8           # WTSConnectState enum index
    
    # Connection states
    WTSActive      = 0
    WTSConnected   = 1
    WTSDisconnected = 4

    # ---- WinAPI prototypes ------------------------------------------------
    WTSEnumerateSessionsW = ctypes.windll.wtsapi32.WTSEnumerateSessionsW
    WTSQuerySessionInformationW = ctypes.windll.wtsapi32.WTSQuerySessionInformationW
    WTSFreeMemory = ctypes.windll.wtsapi32.WTSFreeMemory
    GetLastInputInfo = ctypes.windll.user32.GetLastInputInfo
    GetTickCount64 = getattr(ctypes.windll.kernel32, 'GetTickCount64', None)
    GetTickCount = ctypes.windll.kernel32.GetTickCount

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]

    class WTS_SESSION_INFO(ctypes.Structure):
        _fields_ = [
            ('SessionId', wintypes.DWORD),
            ('pWinStationName', wintypes.LPWSTR),
            ('State', ctypes.c_int)
        ]

    # ------------------------------------------------------------------
    def _time_since_boot() -> float:
        """Seconds since the system booted – used when no interactive user."""
        tick = GetTickCount64() if GetTickCount64 else GetTickCount()
        return tick / 1000.0

    # ------------------------------------------------------------------
    def _get_console_session_id() -> int | None:
        """Find the active console session ID."""
        p_session_info = ctypes.POINTER(WTS_SESSION_INFO)()
        count = wintypes.DWORD()
        
        if not WTSEnumerateSessionsW(
            WTS_CURRENT_SERVER_HANDLE,
            0,  # Reserved
            1,  # Version
            ctypes.byref(p_session_info),
            ctypes.byref(count)
        ):
            logger.debug("useridle: WTSEnumerateSessions failed")
            return None
        
        try:
            sessions = ctypes.cast(
                p_session_info,
                ctypes.POINTER(WTS_SESSION_INFO * count.value)
            ).contents
            
            for session in sessions:
                # Look for Console session that is Active or Connected
                if session.pWinStationName and 'Console' in session.pWinStationName:
                    if session.State in (WTSActive, WTSConnected):
                        logger.debug(f"useridle: Found active console session {session.SessionId}")
                        return session.SessionId
        finally:
            WTSFreeMemory(p_session_info)
        
        logger.debug("useridle: No active console session found")
        return None

    # ------------------------------------------------------------------
    def _get_windows_idle_time() -> float | None:
        """
        Return *real* user-idle seconds for the interactive console session.
        Works when Glances is installed as a Windows service.
        """
        # Find the active console session
        session_id = _get_console_session_id()
        
        if session_id is None:
            logger.debug("useridle: No interactive session – reporting time since boot")
            return _time_since_boot()
        
        # Query session information to verify it's connected
        p_info = ctypes.c_void_p()
        bytes_ret = wintypes.DWORD()
        ok = WTSQuerySessionInformationW(
            WTS_CURRENT_SERVER_HANDLE,
            session_id,
            WTS_CONNECTSTATE_CLASS,
            ctypes.byref(p_info),
            ctypes.byref(bytes_ret)
        )
        
        if ok:
            WTSFreeMemory(p_info)
        else:
            logger.debug(f"useridle: Failed to query session {session_id}")
            return _time_since_boot()
        
        # Get last input info
        lii = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
        if GetLastInputInfo(ctypes.byref(lii)):
            tick = GetTickCount64() if GetTickCount64 else GetTickCount()
            idle_ms = tick - lii.dwTime
            idle_sec = idle_ms / 1000.0
            logger.debug(f"useridle: real idle time = {idle_sec:.1f}s")
            return idle_sec
        else:
            logger.debug("useridle: GetLastInputInfo failed")
            return _time_since_boot()# ----------------------------------------------------------------------
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