"""Base extractor abstract class."""
from abc import ABC, abstractmethod
import httpx
import logging
from typing import Optional
import uuid

logger = logging.getLogger(__name__)


class BaseExtractor(ABC):
    """Abstract base class for all data extractors."""
    
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        """Initialize extractor.
        
        Args:
            client: Optional httpx AsyncClient for reuse.
        """
        self.client = client
        self._should_close_client = client is None
    
    async def fetch(self, url: str) -> bytes:
        """Download raw content from URL.
        
        Args:
            url: URL to fetch.
            
        Returns:
            Raw bytes content.
        """
        if self.client is None:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, follow_redirects=True, timeout=30.0)
                response.raise_for_status()
                return response.content
        else:
            response = await self.client.get(url, follow_redirects=True, timeout=30.0)
            response.raise_for_status()
            return response.content
    
    async def compute_hash(self, content: bytes) -> str:
        """Compute SHA-256 hash of content.
        
        Args:
            content: Raw bytes content.
            
        Returns:
            Hexadecimal hash string.
        """
        from scraper.core.hashing import compute_sha256
        return compute_sha256(content)
    
    @abstractmethod
    async def extract(self, url: str) -> dict:
        """Extract structured data from URL. Must be implemented by subclasses.
        
        Args:
            url: URL to extract data from.
            
        Returns:
            Extracted data as dictionary.
        """
        pass
    
    async def close(self) -> None:
        """Close HTTP client if owned by this extractor."""
        if self._should_close_client and self.client is not None:
            await self.client.aclose()
