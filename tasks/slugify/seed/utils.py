import re

def slugify(text: str) -> str:
    """Convert a heading to a URL-friendly slug."""
    return text.lower().replace(' ', '-')
