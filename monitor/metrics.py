"""Resource metrics for the host/container that runs this dashboard (via psutil)."""
import platform
import socket
import sys
import time

import psutil

_started = time.time()


def host_metrics():
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "cpu_count": psutil.cpu_count(),
        "memory_percent": vm.percent,
        "memory_used_mb": round(vm.used / 1048576),
        "memory_total_mb": round(vm.total / 1048576),
        "disk_percent": disk.percent,
        "host": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "dashboard_uptime_s": round(time.time() - _started),
    }
