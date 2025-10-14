# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
def file_specific_progress(src_path, dst_path, copied_bytes, total_bytes):
    """
    Progress callback that shows filename and progress

    Args:
        src_path: Source builtin path
        dst_path: Destination builtin path
        copied_bytes: Number of bytes copied so far
        total_bytes: Total bytes to copy (None if unknown)
    """
    filename = os.path.basename(src_path)

    if total_bytes:
        percentage = (copied_bytes / total_bytes) * 100
        status = f"{percentage:.1f}%"
    else:
        status = "unknown size"

    print(f"{filename}: {copied_bytes} bytes ({status})")