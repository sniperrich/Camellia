"""
GUI Utility Functions

Provides helper functions for the Camellia.NEL GUI, including port management
and other common utilities.
"""

import socket
from typing import Optional, List


def is_port_available(port: int, host: str = '127.0.0.1') -> bool:
    """
    Check if a port is available for binding.

    Args:
        port: Port number to check
        host: Host address to bind to (default: localhost)

    Returns:
        True if port is available, False otherwise
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return True
    except OSError:
        return False


def find_available_port(start_port: int = 6445, end_port: int = 65535,
                       exclude_ports: Optional[List[int]] = None) -> Optional[int]:
    """
    Find the first available port in the given range.

    Args:
        start_port: Starting port number (default: 6445)
        end_port: Ending port number (default: 65535)
        exclude_ports: List of ports to exclude from search

    Returns:
        First available port number, or None if no port is available
    """
    exclude_ports = exclude_ports or []

    for port in range(start_port, end_port + 1):
        if port in exclude_ports:
            continue
        if is_port_available(port):
            return port

    return None


def get_next_suggested_port(used_ports: List[int], default_port: int = 6445) -> int:
    """
    Get the next suggested port based on currently used ports.

    Strategy:
    - If no ports are used, return default_port
    - Otherwise, return max(used_ports) + 1
    - If that port is not available, scan for next available port

    Args:
        used_ports: List of currently used port numbers
        default_port: Default port to use if no ports are in use

    Returns:
        Suggested port number
    """
    if not used_ports:
        # No ports in use, suggest default
        if is_port_available(default_port):
            return default_port
        # Default port is taken, find next available
        next_port = find_available_port(default_port)
        return next_port if next_port else default_port

    # Find the highest used port and suggest next one
    max_port = max(used_ports)
    suggested_port = max_port + 1

    # Check if suggested port is available
    if suggested_port <= 65535 and is_port_available(suggested_port):
        return suggested_port

    # Suggested port is not available, find next available port
    next_port = find_available_port(suggested_port + 1)
    if next_port:
        return next_port

    # No ports available after max_port, try to find one before it
    next_port = find_available_port(default_port, max_port, exclude_ports=used_ports)
    return next_port if next_port else suggested_port


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to maximum length with suffix.

    Args:
        text: Text to truncate
        max_length: Maximum length including suffix
        suffix: Suffix to append (default: "...")

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix
