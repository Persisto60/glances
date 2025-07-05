"""
Glances plugin for user idle time monitoring.
Supports Windows and Linux (X11).
"""

import ctypes
import sys
from datetime import timedelta

# Try different import paths for different Glances versions
try:
    from glances.plugins.glances_plugin import GlancesPlugin
except ImportError:
    try:
        from glances.plugins.plugin.model import GlancesPluginModel as GlancesPlugin
    except ImportError:
        try:
            from glances.plugins import GlancesPlugin
        except ImportError:
            print("ERROR: Could not import GlancesPlugin - check Glances installation")
            raise

from glances.logger import logger

# Debug: Print when plugin is loaded
print("DEBUG: useridle plugin module loaded")
logger.info("useridle plugin module loaded")

# Windows API structures and functions
if sys.platform.startswith('win'):
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]
        
        def get_windows_idle_time():
            """Get idle time on Windows using GetLastInputInfo API."""
            try:
                last_input = LASTINPUTINFO()
                last_input.cbSize = ctypes.sizeof(last_input)
                if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)):
                    current_tick_count = ctypes.windll.kernel32.GetTickCount()
                    idle_millis = current_tick_count - last_input.dwTime
                    return idle_millis / 1000.0
                return None
            except Exception as e:
                logger.debug(f"useridle: Windows API error: {e}")
                return None
        
        PLATFORM_SUPPORTED = True
    except Exception as e:
        logger.warning(f"useridle: Windows API not available: {e}")
        PLATFORM_SUPPORTED = False
        get_windows_idle_time = None

# Linux - using xprintidle command
elif sys.platform.startswith('linux'):
    import subprocess
    import shutil
    
    def get_linux_idle_time():
        """Get idle time on Linux using xprintidle command."""
        try:
            # Check if xprintidle is available
            if not shutil.which('xprintidle'):
                logger.debug("useridle: xprintidle command not found")
                return None
            
            # Run xprintidle and get the result
            result = subprocess.run(['xprintidle'], 
                                  capture_output=True, 
                                  text=True, 
                                  timeout=5)
            
            if result.returncode == 0:
                # xprintidle returns milliseconds
                idle_ms = int(result.stdout.strip())
                return idle_ms / 1000.0
            else:
                logger.debug(f"useridle: xprintidle failed with return code {result.returncode}")
                return None
                
        except subprocess.TimeoutExpired:
            logger.debug("useridle: xprintidle command timed out")
            return None
        except Exception as e:
            logger.debug(f"useridle: xprintidle error: {e}")
            return None
    
    # Check if xprintidle is available at startup
    PLATFORM_SUPPORTED = shutil.which('xprintidle') is not None
    if not PLATFORM_SUPPORTED:
        logger.debug("useridle: xprintidle not found - install with 'sudo apt install xprintidle'")
        get_linux_idle_time = None

else:
    # Unsupported platform
    PLATFORM_SUPPORTED = False
    get_windows_idle_time = None
    get_linux_idle_time = None


class Plugin(GlancesPlugin):
    """Glances plugin for user idle time."""
    
    def __init__(self, args=None, config=None):
        """Initialize the plugin."""
        print("DEBUG: useridle Plugin.__init__ called")
        super(Plugin, self).__init__(args=args, config=config)
        
        # Plugin settings
        self.plugin_name = "useridle"
        self.display_curse = True
        self.align = 'right'
        
        # Initialize state
        self.idle_seconds = 0.0
        self.error_msg = None
        
        # Check platform support
        if not PLATFORM_SUPPORTED:
            if sys.platform.startswith('win'):
                self.error_msg = "Windows API unavailable"
            elif sys.platform.startswith('linux'):
                self.error_msg = "python-xlib not installed"
            else:
                self.error_msg = f"Unsupported platform: {sys.platform}"
            
            logger.info(f"useridle plugin disabled: {self.error_msg}")
        else:
            logger.info(f"useridle plugin initialized for {sys.platform}")
        
        # Load configuration thresholds (optional)
        self.careful_threshold = self.get_limit('careful', default=300)   # 5 minutes
        self.warning_threshold = self.get_limit('warning', default=600)   # 10 minutes
        self.critical_threshold = self.get_limit('critical', default=1800) # 30 minutes

    def get_key(self):
        """Return the key of this plugin."""
        return 'useridle'

    def update(self):
        """Update plugin statistics."""
        print("DEBUG: useridle update() called")
        
        # Reset stats
        self.stats = {}
        
        if not PLATFORM_SUPPORTED:
            print(f"DEBUG: useridle platform not supported: {self.error_msg}")
            self.stats = {
                'idle_seconds': -1,
                'status': 'disabled',
                'error': self.error_msg
            }
            return self.stats
        
        # Get idle time based on platform
        idle_time = None
        
        if sys.platform.startswith('win') and get_windows_idle_time:
            idle_time = get_windows_idle_time()
        elif sys.platform.startswith('linux') and get_linux_idle_time:
            idle_time = get_linux_idle_time()
            print(f"DEBUG: useridle Linux idle time: {idle_time}")
        
        if idle_time is not None:
            self.idle_seconds = idle_time
            self.stats = {
                'idle_seconds': int(self.idle_seconds),
                'idle_time_formatted': self._format_time(self.idle_seconds),
                'status': self._get_status()
            }
            print(f"DEBUG: useridle stats: {self.stats}")
        else:
            self.idle_seconds = 0.0
            self.stats = {
                'idle_seconds': -1,
                'idle_time_formatted': 'N/A',
                'status': 'error',
                'error': 'Could not retrieve idle time'
            }
            print(f"DEBUG: useridle error stats: {self.stats}")
        
        return self.stats

    def _format_time(self, seconds):
        """Format seconds into human-readable time string."""
        total_seconds = int(seconds)
        
        days = total_seconds // 86400
        remaining = total_seconds % 86400
        hours = remaining // 3600
        remaining = remaining % 3600
        minutes = remaining // 60
        seconds = remaining % 60
        
        if days > 0:
            return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _get_status(self):
        """Get status based on idle time and thresholds."""
        if self.idle_seconds >= self.critical_threshold:
            return 'critical'
        elif self.idle_seconds >= self.warning_threshold:
            return 'warning'
        elif self.idle_seconds >= self.careful_threshold:
            return 'careful'
        else:
            return 'ok'

    def msg_curse(self, args=None, max_width=None):
        """Return the string to display in the curses interface."""
        print("DEBUG: useridle msg_curse() called")
        lines = []
        
        if not PLATFORM_SUPPORTED:
            line = self.curse_add_line(f"Idle: {self.error_msg}")
            lines.append(line)
            print(f"DEBUG: useridle msg_curse platform not supported: {self.error_msg}")
            return lines
        
        if 'error' in self.stats:
            line = self.curse_add_line(f"Idle: {self.stats.get('error', 'Error')}")
            lines.append(line)
            print(f"DEBUG: useridle msg_curse error: {self.stats.get('error')}")
            return lines
        
        # Main display
        idle_time_str = self.stats.get('idle_time_formatted', 'N/A')
        status = self.stats.get('status', 'ok')
        
        print(f"DEBUG: useridle msg_curse display: {idle_time_str}, status: {status}")
        
        # Add color based on status
        if status == 'critical':
            line = self.curse_add_line(f"Idle: {idle_time_str}", self.get_color('critical'))
        elif status == 'warning':
            line = self.curse_add_line(f"Idle: {idle_time_str}", self.get_color('warning'))
        elif status == 'careful':
            line = self.curse_add_line(f"Idle: {idle_time_str}", self.get_color('careful'))
        else:
            line = self.curse_add_line(f"Idle: {idle_time_str}")
        
        lines.append(line)
        print(f"DEBUG: useridle msg_curse returning {len(lines)} lines")
        return lines

    def get_export(self):
        """Return stats for export (API/web interface)."""
        return self.stats

    def get_limit(self, criticity, default=None):
        """Get limit from configuration."""
        try:
            return self.config.get_value(self.plugin_name, criticity, default=default)
        except Exception:
            return default