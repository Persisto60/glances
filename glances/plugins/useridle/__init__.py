# useridle last mod:03/11/2025
#
# This file is part of Glances.
#
# written by Pete BOS and friends (Gemini, Claude and Chatgpt)
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

# Corrected import path for GlancesPluginModel and logger
from glances.plugins.plugin.model import GlancesPluginModel
from glances.logger import logger


# --- Windows-specific API ---
if sys.platform.startswith('win'):
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]

    # def _get_windows_idle_time():
    #     last_input = LASTINPUTINFO()
    #     last_input.cbSize = ctypes.sizeof(last_input)
    #     if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)):
    #         current_tick_count = ctypes.windll.kernel32.GetTickCount()
    #         idle_millis = current_tick_count - last_input.dwTime
    #         return idle_millis / 1000.0
    #     return None  # Return None on error


    def _get_windows_idle_time():
        """
        Gets idle time. Returns time since boot if no user session is detected,
        otherwise uses GetLastInputInfo.
        """
        try:
            # Check the current session ID
            current_session_id = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
        except AttributeError:
            # Fallback if WTSGetActiveConsoleSessionId is not available (e.g., older OS)
            current_session_id = None
        
        # If the current session is not an interactive console session (i.e., it's a service session)
        if current_session_id == 0xFFFFFFFF or current_session_id == 0:
            # No interactive user session, return time since boot
            current_tick_count = ctypes.windll.kernel32.GetTickCount64() if hasattr(ctypes.windll.kernel32, 'GetTickCount64') else ctypes.windll.kernel32.GetTickCount()
            boot_time = datetime.now() - timedelta(milliseconds=current_tick_count)
            idle_seconds = (datetime.now() - boot_time).total_seconds()
            logger.debug(f"useridle: No interactive user session detected. Reporting time since boot: {int(idle_seconds)}s")
            return idle_seconds
        
        # An interactive session is active, use GetLastInputInfo
        last_input = LASTINPUTINFO()
        last_input.cbSize = ctypes.sizeof(last_input)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)):
            current_tick_count = ctypes.windll.kernel32.GetTickCount64() if hasattr(ctypes.windll.kernel32, 'GetTickCount64') else ctypes.windll.kernel32.GetTickCount()
            idle_millis = current_tick_count - last_input.dwTime
            logger.debug(f"useridle: Interactive session active. Reporting GetLastInputInfo time: {idle_millis/1000.0}s")
            return idle_millis / 1000.0
        
        logger.error("useridle: GetLastInputInfo failed.")
        return None # Return None on error

# --- Linux-specific API ---
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
        Gets idle time. Returns time since boot if no Xorg session is detected,
        otherwise uses xprintidle.
        """
        # Check if an Xorg session is active
        if 'DISPLAY' not in os.environ:
            # No Xorg session, return time since boot
            idle_seconds = (datetime.now() - _BOOT_TIME).total_seconds()
            logger.debug(f"useridle: No Xorg session detected. Reporting time since boot: {int(idle_seconds)}s")
            return idle_seconds
        
        # An Xorg session is active, try to use xprintidle
        if not _check_xprintidle_availability():
            logger.debug(
                "useridle: xprintidle not available, skipping idle time check.")
            return None

        try:
            # Run xprintidle to get the result
            result = subprocess.run(['xprintidle'],
                                    capture_output=True,
                                    text=True,
                                    timeout=5)

            if result.returncode == 0:
                # xprintidle returns milliseconds
                idle_ms = int(result.stdout.strip())
                logger.debug(f"useridle: Xorg session active. Reporting xprintidle time: {idle_ms/1000.0}s")
                return idle_ms / 1000.0
            else:
                logger.debug(
                    f"useridle: xprintidle failed with return code {result.returncode}. Error: {result.stderr.strip()}")
                return None

        except FileNotFoundError:
            logger.warning(
                "useridle: xprintidle command not found. This should have been caught by initial check.")
            return None
        except subprocess.TimeoutExpired:
            logger.debug("useridle: xprintidle command timed out.")
            return None
        except ValueError:
            logger.error(
                f"useridle: Could not parse xprintidle output: '{result.stdout.strip()}' is not a valid number.")
            return None
        except Exception as e:
            logger.error(
                f"useridle: An unexpected error occurred while running xprintidle: {e}", exc_info=False)
            return None
else:  # Other operating systems (macOS, BSD, etc.)
    _get_windows_idle_time = None  # Mark as unavailable
    _get_linux_idle_time = None  # Mark as unavailable


# --- Glances Plugin Model ---
class PluginModel(GlancesPluginModel):
    """
    Glances plugin to detect user idle time.
    Supports Windows (via GetLastInputInfo) and Linux (via xprintidle).
    """

    def __init__(self, args=None, config=None):
        super().__init__(args=args, config=config)
        logger.debug("useridle: useridle loaded).")

        self.display_curse = True
        self.align = 'right'

        self.platform = sys.platform
        self.idle_seconds = 0.0
        self.idle_timedelta = timedelta(seconds=0)

        # Set initial status and disabled message based on platform capabilities
        if self.platform.startswith('win'):
            self.disabled_msg = None
            if _get_windows_idle_time is None:
                self.disabled_msg = "Windows API not available."
                self.set_disabled()

        elif self.platform.startswith('linux'):
            # On Linux, plugin is always active to report either boot time or xprintidle
            self.disabled_msg = None
            
        else:  # Unsupported OS
            self.disabled_msg = "Unsupported OS."
            self.set_disabled()

        if self.is_disabled():
            logger.info(f"useridle plugin: Disabled ({self.disabled_msg}).")
        else:
            logger.info(f"useridle plugin: Initialized for {self.platform}.")

        # Thresholds
        self.careful_threshold = self.get_limit('careful')
        self.warning_threshold = self.get_limit('warning')
        self.critical_threshold = self.get_limit('critical')

    def get_export(self):
        """
        Export idle time in seconds.
        """
        if self.is_disabled():
            return {'seconds': -1, 'status': 'disabled', 'reason': self.disabled_msg}
        return {'seconds': int(self.idle_seconds), 'status': self.get_stats()}

    @GlancesPluginModel._check_decorator
    @GlancesPluginModel._log_result_decorator
    def update(self):
        """
        Update the plugin data.
        """
        if self.is_disabled():
            self.stats = self.disabled_msg or "N/A"
            return self.stats

        idle_time_s = None

        if self.platform.startswith('win'):
            idle_time_s = _get_windows_idle_time()
        elif self.platform.startswith('linux'):
            # The Linux function returns time since boot if no X session is found,
            # or xprintidle result, or None on specific errors.
            idle_time_s = _get_linux_idle_time()

        # --- This block processes valid idle time data (including time since boot on Linux) ---
        if idle_time_s is not None:
            self.idle_seconds = idle_time_s
            self.idle_timedelta = timedelta(seconds=int(self.idle_seconds))

            # Format as H:MM:SS (or D days, H:MM:SS)
            total_seconds = int(self.idle_seconds)
            days = total_seconds // (24 * 3600)
            remaining_seconds = total_seconds % (24 * 3600)
            hours = remaining_seconds // 3600
            remaining_seconds %= 3600
            minutes = remaining_seconds // 60
            seconds = remaining_seconds % 60

            if days > 0:
                self.stats = f"{days}d {hours:02}:{minutes:02}:{seconds:02}"
            else:
                self.stats = f"{hours:02}:{minutes:02}:{seconds:02}"

        # --- This block handles actual errors (where idle_time_s is None) ---
        else:
            self.idle_seconds = 0.0
            self.idle_timedelta = timedelta(seconds=0)
            self.stats = "N/A"  # Indicate no data / error
            
            if self.platform.startswith('win') and idle_time_s is None:
                logger.debug("useridle: Cannot get idle time (Windows API error).")
            else:
                # General error message (This will catch xprintidle failures)
                logger.error("useridle: Could not retrieve idle time. Displaying 'N/A'.")

        return self.stats

    def msg_curse(self, args=None, max_width=None):
        """
        Return the string to display in the curses interface.
        """
        # Init the return message
        ret = []

        # Only process if plugin is not disabled
        if self.is_disabled():
            logger.debug(
                "UserIdle plugin is disabled, returning empty display message.")
            return ret  # Return an empty list when disabled, similar to uptime

        # Check for "N/A" status, which usually indicates an error or unavailability
        if self.stats == "N/A":
            error_msg = f"UserIdle: {self.stats} (Error or not available on this system)."
            logger.debug(
                f"UserIdle plugin reporting 'N/A' stats. Message: '{error_msg}'")
            # Returning an empty list makes it consistent with disabled state for width calculation.
            return ret  # Returning empty list for "N/A" too, to ensure width is 0 if not active

        return [self.curse_add_line(f"UI IDLE: {self.stats}")]

    def get_name(self):
        return "useridle"