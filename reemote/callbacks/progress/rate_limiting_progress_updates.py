import time


class Rate_limiting_progress_updates:
    """
    Progress handler that throttles updates to avoid flooding

    Args:
        src_path: Source file path
        dst_path: Destination file path
        copied_bytes: Number of bytes copied so far
        total_bytes: Total bytes to copy (None if unknown)
    """
    def __init__(self, min_interval=0.1):  # Update at most every 100ms
        self.min_interval = min_interval
        self.last_update = 0

    def __call__(self, src_path, dst_path, copied_bytes, total_bytes):
        current_time = time.time()
        if current_time - self.last_update >= self.min_interval:
            if total_bytes:
                percentage = (copied_bytes / total_bytes) * 100
                print(f"Progress: {percentage:.1f}% ({copied_bytes}/{total_bytes} bytes)")
            else:
                print(f"Progress: {copied_bytes} bytes copied")
            self.last_update = current_time


