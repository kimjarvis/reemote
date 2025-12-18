import logging
from reemote.config import Config


def reemote_logging():
    config = Config()


    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG,
        filename=config.get_logging(),
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Create a named logger "reemote"
    logger = logging.getLogger("reemote")
    logger.setLevel(logging.DEBUG)  # Set desired log level for your logger

    # Suppress asyncssh logs by setting its log level to WARNING or higher
    logging.getLogger("asyncssh").setLevel(logging.WARNING)
