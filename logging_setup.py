"""Shared logging configuration for the scraper and web server."""

import logging
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


class ISTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, IST).strftime("%Y-%m-%d %H:%M:%S IST")


def setup_logging(level=logging.INFO):
    """Install the RedFetcher console handler once, preserving host handlers."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(ISTFormatter("%(asctime)s %(levelname)-8s %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
