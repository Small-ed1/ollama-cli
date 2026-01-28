"""System tools for basic system operations.

This module provides fundamental system tools like getting the current time
and reading files, which don't require any external dependencies.
"""

import datetime
import json
import os
from typing import Any, Dict, List, Optional


def tool_get_time(tz: str = "local") -> str:
    """Get current time and timezone information.
    
    Args:
        tz: Timezone identifier (e.g., 'local', 'UTC', 'America/New_York')
        
    Returns:
        JSON string with time information
    """
    try:
        if tz == "local":
            now = datetime.datetime.now()
            tzinfo = now.astimezone().tzinfo
            tz_name = tzinfo.tzname(now) if tzinfo else "Local"
            offset = now.astimezone().utcoffset()
            if offset is not None:
                offset_str = f"UTC{offset.total_seconds()/3600:+.0f}"
            else:
                offset_str = "UTC"
        elif tz == "UTC":
            now = datetime.datetime.now(datetime.timezone.utc)
            tz_name = "UTC"
            offset_str = "UTC"
        else:
            import zoneinfo
            tzinfo = zoneinfo.ZoneInfo(tz)
            now = datetime.datetime.now(tzinfo)
            tz_name = tz
            offset = now.utcoffset()
            if offset is not None:
                offset_str = f"UTC{offset.total_seconds()/3600:+.0f}"
            else:
                offset_str = "UTC"
        
        time_info = {
            "datetime": now.isoformat(),
            "timestamp": now.timestamp(),
            "timezone": tz_name,
            "utc_offset": offset_str,
            "year": now.year,
            "month": now.month,
            "day": now.day,
            "hour": now.hour,
            "minute": now.minute,
            "second": now.second,
        }
        
        return json.dumps(time_info, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Failed to get time: {e}"}, ensure_ascii=False)


def tool_read_file(path: str, max_bytes: int = 8000, allowed_paths: Optional[List[str]] = None, 
                    file_security: Optional[Dict[str, Any]] = None) -> str:
    """Read contents of a file with safety checks.
    
    Args:
        path: File path to read
        max_bytes: Maximum bytes to read
        allowed_paths: List of allowed base paths (None = unsafe mode disabled)
        
    Returns:
        JSON string with file content or error
    """
    try:
        # Security: disallow reading sensitive system files
        dangerous_paths = [
            "/etc/", "/proc/", "/sys/", "/dev/",
            "/boot/", "/root/", "/var/log/",
            os.path.expanduser("~/.ssh/"),
            "/usr/bin/", "/bin/", "/sbin/",
        ]
        
        # Check if path is dangerous
        for dangerous in dangerous_paths:
            if path.startswith(dangerous):
                return json.dumps({
                    "error": f"Access denied: dangerous path {path}",
                    "path": path
                }, ensure_ascii=False)
        
        # Use file_security if provided, otherwise use legacy checks
        if file_security is not None:
            allowed_paths = file_security.get("allowed_paths")
            unsafe_mode = file_security.get("unsafe_mode", False)
            
            # Skip all checks if in unsafe mode
            if not unsafe_mode:
                # Check allowed paths if specified
                if allowed_paths is not None:
                    # Normalize path for comparison
                    abs_path = os.path.abspath(path)
                    allowed = False
                    for allowed_path in allowed_paths:
                        if abs_path.startswith(os.path.abspath(allowed_path)):
                            allowed = True
                            break
                    if not allowed:
                        return json.dumps({
                            "error": f"Access denied: path {path} not in allowed paths",
                            "path": path,
                            "allowed_paths": allowed_paths
                        }, ensure_ascii=False)
        else:
            # Legacy path checking if no file_security provided
            if allowed_paths is not None:
                # Normalize path for comparison
                abs_path = os.path.abspath(path)
                allowed = False
                for allowed_path in allowed_paths:
                    if abs_path.startswith(os.path.abspath(allowed_path)):
                        allowed = True
                        break
                if not allowed:
                    return json.dumps({
                        "error": f"Access denied: path {path} not in allowed paths",
                        "path": path,
                        "allowed_paths": allowed_paths
                    }, ensure_ascii=False)
        
        # Validate path is safe (basic security check)
        if not os.path.isfile(path):
            return json.dumps({"error": f"File not found: {path}"}, ensure_ascii=False)
        
        # Check file size first
        file_size = os.path.getsize(path)
        if file_size > max_bytes:
            return json.dumps({
                "error": f"File too large: {file_size} bytes (max: {max_bytes})",
                "path": path,
                "size": file_size,
                "max_allowed": max_bytes
            }, ensure_ascii=False)
        
        # Read file content
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read(max_bytes)
        
        return json.dumps({
            "path": path,
            "content": content,
            "size": len(content),
            "encoding": "utf-8"
        }, indent=2, ensure_ascii=False)
        
    except PermissionError:
        return json.dumps({"error": f"Permission denied: {path}"}, ensure_ascii=False)
    except UnicodeDecodeError as e:
        return json.dumps({"error": f"Encoding error reading {path}: {e}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Failed to read file {path}: {e}"}, ensure_ascii=False)