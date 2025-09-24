def progress_bar(src_path, dst_path, copied_bytes, total_bytes):
    """
    Progress callback with ASCII progress bar

    Args:
        src_path: Source file path
        dst_path: Destination file path
        copied_bytes: Number of bytes copied so far
        total_bytes: Total bytes to copy (None if unknown)
    """
    if total_bytes and total_bytes > 0:
        bar_length = 40
        filled_length = int(bar_length * copied_bytes // total_bytes)
        bar = '█' * filled_length + '-' * (bar_length - filled_length)
        percentage = (copied_bytes / total_bytes) * 100
        print(f'\r{bar} {percentage:.1f}% | {copied_bytes}/{total_bytes} bytes', end='', flush=True)

        # Print newline when transfer completes
        if copied_bytes == total_bytes:
            print()