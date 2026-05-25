"""Hashing utilities for deduplication."""
import hashlib
from typing import Union


def compute_sha256(content: Union[bytes, str]) -> str:
    """Compute SHA-256 hash of content.
    
    Args:
        content: Bytes or string to hash.
        
    Returns:
        Hexadecimal hash string.
    """
    if isinstance(content, str):
        content = content.encode('utf-8')
    return hashlib.sha256(content).hexdigest()
