import ctypes
import json

driver = ctypes.CDLL("libcuda.so.1")
status = driver.cuInit(0)
count = ctypes.c_int()
count_status = driver.cuDeviceGetCount(ctypes.byref(count))
print(json.dumps({"cuInit": status, "cuDeviceGetCount": count_status, "devices": count.value}), flush=True)
if status != 0 or count_status != 0 or count.value < 1:
    raise RuntimeError("CUDA driver initialization failed")
