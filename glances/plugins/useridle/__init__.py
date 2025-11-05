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
    import subprocess
    import json

    # ---- Windows constants ------------------------------------------------
    WTS_CURRENT_SERVER_HANDLE = 0
    
    # ---- WinAPI prototypes ------------------------------------------------
    WTSEnumerateSessionsW = ctypes.windll.wtsapi32.WTSEnumerateSessionsW
    WTSQuerySessionInformationW = ctypes.windll.wtsapi32.WTSQuerySessionInformationW
    WTSFreeMemory = ctypes.windll.wtsapi32.WTSFreeMemory
    GetTickCount64 = getattr(ctypes.windll.kernel32, 'GetTickCount64', None)
    GetTickCount = ctypes.windll.kernel32.GetTickCount

    # Connection states
    WTSActive = 0

    class WTS_SESSION_INFO(ctypes.Structure):
        _fields_ = [
            ('SessionId', wintypes.DWORD),
            ('pWinStationName', wintypes.LPWSTR),
            ('State', ctypes.c_int)
        ]

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]

    # ------------------------------------------------------------------
    def _time_since_boot() -> float:
        """Seconds since the system booted."""
        tick = GetTickCount64() if GetTickCount64 else GetTickCount()
        return tick / 1000.0

    # ------------------------------------------------------------------
    def _get_active_console_session() -> int | None:
        """Find the active console session ID."""
        p_session_info = ctypes.POINTER(WTS_SESSION_INFO)()
        count = wintypes.DWORD()
        
        if not WTSEnumerateSessionsW(
            WTS_CURRENT_SERVER_HANDLE, 0, 1,
            ctypes.byref(p_session_info),
            ctypes.byref(count)
        ):
            return None
        
        try:
            sessions = ctypes.cast(
                p_session_info,
                ctypes.POINTER(WTS_SESSION_INFO * count.value)
            ).contents
            
            for session in sessions:
                if session.State == WTSActive and session.SessionId > 0:
                    logger.debug(f"useridle: Found active session {session.SessionId}")
                    return session.SessionId
        finally:
            WTSFreeMemory(p_session_info)
        
        return None

    # ------------------------------------------------------------------
    def _get_idle_via_powershell(session_id: int) -> float | None:
        """
        Use PowerShell to get idle time - works from service context.
        This queries the user session using quser command.
        """
        try:
            # Use quser to get session idle time
            result = subprocess.run(
                ['quser'],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            if result.returncode != 0:
                logger.debug(f"useridle: quser failed: {result.stderr}")
                return None
            
            # Parse quser output
            lines = result.stdout.strip().split('\n')
            if len(lines) < 2:
                return None
            
            for line in lines[1:]:  # Skip header
                parts = line.split()
                if len(parts) < 5:
                    continue
                
                # Idle time is usually in column index 4 or 5
                # Format can be ".", "0:01", "2:30", or "1+23:45"
                idle_str = parts[4] if parts[1] != '>' else parts[5] if len(parts) > 5 else parts[4]
                
                if idle_str == '.' or idle_str.lower() == 'none':
                    # Active (less than 1 minute idle)
                    return 0.0
                
                # Parse idle time
                if '+' in idle_str:
                    # Format: "days+hours:minutes"
                    days_part, time_part = idle_str.split('+')
                    days = int(days_part)
                    hours, minutes = map(int, time_part.split(':'))
                    return days * 86400 + hours * 3600 + minutes * 60
                elif ':' in idle_str:
                    # Format: "hours:minutes" or "minutes:seconds"
                    parts_time = idle_str.split(':')
                    if len(parts_time) == 2:
                        # Could be hours:minutes or minutes:seconds
                        # quser typically shows minutes for < 1 hour
                        val1, val2 = map(int, parts_time)
                        if val1 < 24:  # Likely minutes:seconds or hours:minutes
                            return val1 * 60 + val2
                        else:
                            return val1 * 3600 + val2 * 60
                else:
                    # Just a number (minutes)
                    return int(idle_str) * 60
                    
        except Exception as e:
            logger.debug(f"useridle: quser parsing failed: {e}")
            return None
        
        return None

    # ------------------------------------------------------------------
    def _get_windows_idle_time() -> float | None:
        """
        Return user-idle seconds. Works when running as a Windows service.
        Uses multiple fallback methods.
        """
        # Check if any user is logged in
        session_id = _get_active_console_session()
        
        if session_id is None:
            logger.debug("useridle: No active session")
            return _time_since_boot()
        
        # Verify user is logged in
        p_buffer = ctypes.c_void_p()
        bytes_returned = wintypes.DWORD()
        
        if WTSQuerySessionInformationW(
            WTS_CURRENT_SERVER_HANDLE, session_id, 5,  # WTSUserName
            ctypes.byref(p_buffer), ctypes.byref(bytes_returned)
        ):
            try:
                username = ctypes.wstring_at(p_buffer)
                WTSFreeMemory(p_buffer)
                
                if not username:
                    logger.debug(f"useridle: Session {session_id} has no user")
                    return _time_since_boot()
                
                logger.debug(f"useridle: Session {session_id} user: {username}")
            except:
                WTSFreeMemory(p_buffer)
                return _time_since_boot()
        else:
            return _time_since_boot()
        
        # Method 1: Try GetLastInputInfo (works if service has right permissions)
        try:
            lii = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                tick = GetTickCount64() if GetTickCount64 else GetTickCount()
                idle_ms = tick - lii.dwTime
                idle_sec = idle_ms / 1000.0
                
                uptime = _time_since_boot()
                # Check if result is valid
                if 0 <= idle_sec <= uptime and idle_sec < uptime * 0.99:
                    logger.debug(f"useridle: GetLastInputInfo returned {idle_sec:.1f}s")
                    return idle_sec
                else:
                    logger.debug(f"useridle: GetLastInputInfo returned suspicious value {idle_sec:.1f}s (uptime={uptime:.1f}s)")
        except Exception as e:
            logger.debug(f"useridle: GetLastInputInfo failed: {e}")
        
        # Method 2: Try quser command
        idle_quser = _get_idle_via_powershell(session_id)
        if idle_quser is not None:
            logger.debug(f"useridle: quser returned {idle_quser:.1f}s")
            return idle_quser
        
        # Method 3: Fallback - assume user is active if we found a session
        # This is not ideal but prevents false positives
        logger.warning("useridle: All methods failed, assuming user is active (returning 0)")
        return 0.0
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