# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
def basis_progress(src_path, dst_path, copied_bytes, total_bytes):
    """
    Progress callback for SFTP builtin transfers.

    Args:
        src_path: Source builtin path
        dst_path: Destination builtin path
        copied_bytes: Number of bytes copied so far
        total_bytes: Total bytes to copy (None if unknown)
    """
    if total_bytes:
        percentage = (copied_bytes / total_bytes) * 100
        print(f"Transferring: {src_path} -> {dst_path} [{copied_bytes}/{total_bytes} bytes] {percentage:.1f}%")
    else:
        print(f"Transferring: {src_path} -> {dst_path} [{copied_bytes} bytes copied]")