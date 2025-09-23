def basis_progress(src_path, dst_path, copied_bytes, total_bytes):
    """
    Progress callback for SFTP file transfers.

    Args:
        src_path: Source file path
        dst_path: Destination file path
        copied_bytes: Number of bytes copied so far
        total_bytes: Total bytes to copy (None if unknown)
    """
    if total_bytes:
        percentage = (copied_bytes / total_bytes) * 100
        print(f"Transferring: {src_path} -> {dst_path} [{copied_bytes}/{total_bytes} bytes] {percentage:.1f}%")
    else:
        print(f"Transferring: {src_path} -> {dst_path} [{copied_bytes} bytes copied]")