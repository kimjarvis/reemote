import logging

def setup_logging():
    logging.basicConfig(
        level=logging.DEBUG,
        filename="logging.log",
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Create a named logger "reemote"
    logger = logging.getLogger("reemote")
    logger.setLevel(logging.DEBUG)  # Set desired log level for your logger

    # Suppress asyncssh logs by setting its log level to WARNING or higher
    logging.getLogger("asyncssh").setLevel(logging.WARNING)
