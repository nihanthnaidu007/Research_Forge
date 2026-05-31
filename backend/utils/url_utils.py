"""
ResearchForge URL utility helpers.
Shared functions for URL parsing and domain extraction.
"""

from urllib.parse import urlparse


def extract_domain(url: str) -> str:
    """
    Extract the bare domain name from a URL.
    Strips www. prefix if present.
    Returns 'unknown' on parse failure.
    """
    try:
        parsed = urlparse(url)
        return parsed.netloc.replace("www.", "")
    except Exception:
        return "unknown"
