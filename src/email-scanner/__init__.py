"""Email scanner: crawl a website and list every email with its best page."""

__version__ = "1.1.0"

from .core import EmailResult, ScanResult, scan  # noqa: E402

__all__ = ["EmailResult", "ScanResult", "scan", "__version__"]
