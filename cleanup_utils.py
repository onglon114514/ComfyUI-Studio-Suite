import gc
import os


def get_cuda_memory_snapshot():
    try:
        import torch

        if not torch.cuda.is_available():
            return {"available": False}
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        return {
            "available": True,
            "free_mb": int(free_bytes // (1024 * 1024)),
            "total_mb": int(total_bytes // (1024 * 1024)),
            "allocated_mb": int(torch.cuda.memory_allocated() // (1024 * 1024)),
            "reserved_mb": int(torch.cuda.memory_reserved() // (1024 * 1024)),
        }
    except Exception as error:
        return {"available": False, "error": str(error)}


def get_system_memory_snapshot():
    if os.name != "nt":
        return {"available": False, "reason": "unsupported_platform"}
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {"available": False, "error": "GlobalMemoryStatusEx failed"}
        return {
            "available": True,
            "memory_load_percent": int(status.dwMemoryLoad),
            "total_phys_mb": int(status.ullTotalPhys // (1024 * 1024)),
            "avail_phys_mb": int(status.ullAvailPhys // (1024 * 1024)),
            "total_pagefile_mb": int(status.ullTotalPageFile // (1024 * 1024)),
            "avail_pagefile_mb": int(status.ullAvailPageFile // (1024 * 1024)),
        }
    except Exception as error:
        return {"available": False, "error": str(error)}


def get_process_memory_snapshot():
    try:
        import psutil

        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        return {
            "available": True,
            "source": "psutil",
            "rss_mb": int(memory_info.rss // (1024 * 1024)),
            "vms_mb": int(memory_info.vms // (1024 * 1024)),
            "private_mb": int(getattr(memory_info, "private", 0) // (1024 * 1024)),
        }
    except Exception:
        pass

    if os.name != "nt":
        return {"available": False, "reason": "unsupported_platform"}
    try:
        import ctypes

        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        psapi = ctypes.WinDLL("psapi")
        get_process_memory_info = psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), ctypes.c_ulong]
        get_process_memory_info.restype = ctypes.c_bool
        if not get_process_memory_info(handle, ctypes.byref(counters), counters.cb):
            return {"available": False, "error": "GetProcessMemoryInfo failed"}
        return {
            "available": True,
            "source": "psapi",
            "working_set_mb": int(counters.WorkingSetSize // (1024 * 1024)),
            "peak_working_set_mb": int(counters.PeakWorkingSetSize // (1024 * 1024)),
            "pagefile_usage_mb": int(counters.PagefileUsage // (1024 * 1024)),
            "peak_pagefile_usage_mb": int(counters.PeakPagefileUsage // (1024 * 1024)),
            "private_usage_mb": int(counters.PrivateUsage // (1024 * 1024)),
        }
    except Exception as error:
        return {"available": False, "error": str(error)}


def get_combined_memory_snapshot():
    return {
        "cuda": get_cuda_memory_snapshot(),
        "system": get_system_memory_snapshot(),
        "process": get_process_memory_snapshot(),
    }


def trim_process_working_set():
    if os.name != "nt":
        return {"attempted": False, "reason": "unsupported_platform"}
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetCurrentProcess()
        try:
            psapi = ctypes.WinDLL("psapi")
            empty_working_set = psapi.EmptyWorkingSet
            empty_working_set.argtypes = [ctypes.c_void_p]
            empty_working_set.restype = ctypes.c_bool
            if empty_working_set(handle):
                return {"attempted": True, "status": "ok", "method": "EmptyWorkingSet"}
        except Exception:
            pass

        set_working_set_size = ctypes.windll.kernel32.SetProcessWorkingSetSize
        set_working_set_size.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
        set_working_set_size.restype = ctypes.c_bool
        max_size_t = ctypes.c_size_t(-1).value
        if set_working_set_size(handle, max_size_t, max_size_t):
            return {"attempted": True, "status": "ok", "method": "SetProcessWorkingSetSize"}
        return {"attempted": True, "status": "failed"}
    except Exception as error:
        return {"attempted": True, "status": "failed", "error": str(error)}


def cleanup_comfy_resources(*, unload_models=True, clear_torch_cache=True, trim_working_set=True):
    memory_before = get_combined_memory_snapshot()
    result = {
        "memory_before": memory_before,
        "comfy_unload": "skipped",
        "torch_cache": "skipped",
        "trim_result": {"attempted": False, "reason": "disabled"},
    }

    if unload_models:
        try:
            import comfy.model_management as model_management

            current_loaded_models = getattr(model_management, "current_loaded_models", None)
            if isinstance(current_loaded_models, list):
                result["comfy_loaded_models_before"] = len(current_loaded_models)
            unload_all = getattr(model_management, "unload_all_models", None)
            cleanup_models_gc = getattr(model_management, "cleanup_models_gc", None)
            soft_empty_cache = getattr(model_management, "soft_empty_cache", None)
            if callable(unload_all):
                unload_all()
                result["comfy_unload"] = "ok"
            else:
                result["comfy_unload"] = "unload_all_models_not_available"
            if callable(cleanup_models_gc):
                cleanup_models_gc()
                result["comfy_cleanup_models_gc"] = "ok"
            if callable(soft_empty_cache):
                try:
                    soft_empty_cache(force=True)
                except TypeError:
                    soft_empty_cache()
                result["comfy_soft_empty_cache"] = "ok"
            if isinstance(current_loaded_models, list):
                result["comfy_loaded_models_after"] = len(current_loaded_models)
        except Exception as error:
            result["comfy_unload"] = f"failed: {error}"

    gc.collect()

    if clear_torch_cache:
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                result["torch_cache"] = "ok"
            else:
                result["torch_cache"] = "cuda_unavailable"
        except Exception as error:
            result["torch_cache"] = f"failed: {error}"

    result["memory_after_gc"] = get_combined_memory_snapshot()
    if trim_working_set:
        result["trim_result"] = trim_process_working_set()
    result["memory_after_trim"] = get_combined_memory_snapshot()
    return result
