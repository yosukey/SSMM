# system_info.py
import platform
import subprocess
import sys

import PIL
import PySide6
import imagehash
import psutil
import toml

from ssmm import config
from ssmm import pdf_utils
from ssmm.utils import get_ffmpeg_path, get_ffmpeg_source


def collect_system_info(app_version: str, available_encoders_map: dict) -> str:
    """Plain-text system report prepended to an exported debug log."""
    def _bytes_to_gb(bytes_val):
        return round(bytes_val / (1024 ** 3), 2)

    info_lines = [
        "================== System Information ==================",
        f"App Version: {app_version}",
        f"Platform: {platform.platform()}",
        f"Architecture: {platform.machine()}",
        f"Python Version: {sys.version}",
    ]

    cpu_info_lines = ["---------------------- CPU -----------------------"]
    try:
        cpu_info_lines.append(f"  Model: {platform.processor()}")
        cpu_info_lines.append(f"  Physical Cores: {psutil.cpu_count(logical=False)}")
        cpu_info_lines.append(f"  Logical Cores: {psutil.cpu_count(logical=True)}")
        cpu_freq = psutil.cpu_freq()
        if cpu_freq:
            cpu_info_lines.append(f"  Max Frequency: {cpu_freq.max:.2f} Mhz")
    except Exception as e:
        cpu_info_lines.append(f"  Could not get detailed CPU info: {e}")
    info_lines.extend(cpu_info_lines)

    memory_info_lines = ["--------------------- Memory ---------------------"]
    try:
        vmem = psutil.virtual_memory()
        memory_info_lines.append(f"  Total: {_bytes_to_gb(vmem.total)} GB")
        memory_info_lines.append(f"  Available: {_bytes_to_gb(vmem.available)} GB")
        memory_info_lines.append(f"  Used: {_bytes_to_gb(vmem.used)} GB ({vmem.percent}%)")
    except Exception as e:
        memory_info_lines.append(f"  Could not get memory info: {e}")
    info_lines.extend(memory_info_lines)
    
    disk_info_lines = ["------------------ Disk Partitions -----------------"]
    try:
        partitions = psutil.disk_partitions()
        for part in partitions:
            # Skip optical drives and unformatted partitions
            if 'cdrom' in part.opts or not part.fstype:
                continue
            disk_info_lines.append(f"  Device: {part.device} (Mount: {part.mountpoint}, FSType: {part.fstype})")
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disk_info_lines.append(f"    Total: {_bytes_to_gb(usage.total)} GB")
                disk_info_lines.append(f"    Used: {_bytes_to_gb(usage.used)} GB ({usage.percent}%)")
                disk_info_lines.append(f"    Free: {_bytes_to_gb(usage.free)} GB")
            except Exception:
                disk_info_lines.append("    Could not retrieve usage for this partition.")
    except Exception as e:
        disk_info_lines.append(f"  Could not get disk partitions: {e}")
    info_lines.extend(disk_info_lines)

    libs_info_lines = ["----------------- Library Versions -----------------"]
    libs_to_check = {
        "pypdfium2": pdf_utils.pypdfium2_version(),
        "Pillow": PIL,
        "PySide6": PySide6,
        "toml": toml,
        "Imagehash": imagehash
    }
    for name, lib in libs_to_check.items():
        try:
            version = lib if isinstance(lib, str) else getattr(lib, '__version__', 'N/A')
            libs_info_lines.append(f"  {name}: {version}")
        except Exception:
            libs_info_lines.append(f"  {name}: Error getting version")
    info_lines.extend(libs_info_lines)

    info_lines.append("------------------ FFmpeg Information ------------------")
    try:
        ffmpeg_path = get_ffmpeg_path()
        
        result = subprocess.run(
            [str(ffmpeg_path), "-version"], 
            capture_output=True, text=True, timeout=5, 
            encoding='utf-8', errors='replace',
            creationflags=config.SUBPROCESS_CREATION_FLAGS
        )
        ffmpeg_version = result.stdout.splitlines()[0].strip() if result.stdout else "N/A"
        info_lines.append(f"Source: {get_ffmpeg_source()}")
        info_lines.append(f"Version: {ffmpeg_version}")
        info_lines.append(f"Path: {str(ffmpeg_path)}")
    except Exception as e:
        info_lines.append(f"Could not retrieve FFmpeg info: {e}")

    info_lines.append("----------------- Available Encoders -----------------")
    if available_encoders_map:
        for codec, encoders in sorted(available_encoders_map.items()):
            info_lines.append(f"  {codec}: {', '.join(encoders)}")
    else:
        info_lines.append("No functional encoders detected or test not run yet.")

    info_lines.append("========================================================")
    
    return "\n".join(info_lines)
