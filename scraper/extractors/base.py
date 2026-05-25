"""Base extractor abstract class."""
from abc import ABC, abstractmethod
import httpx
import logging
from typing import Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

logger = logging.getLogger(__name__)


class BaseExtractor(ABC):
    """Abstract base class for all data extractors.
    
    Implements the contract defined in AGENTS.md:
    - fetch(): Download raw content from URL
    - compute_hash(): Compute SHA-256 for deduplication
    - has_changed(): Check if content has changed since last fetch
    - save_raw(): Persist to Bronze layer
    """
    
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
            
        Raises:
            httpx.HTTPError: If request fails.
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
            Hexadecimal hash string (64 chars).
        """
        from scraper.core.hashing import compute_sha256
        return compute_sha256(content)
    
    async def has_changed(self, db: AsyncSession, url: str, hash_contenido: str) -> bool:
        """Check if content has changed since last fetch.
        
        Queries DocumentoRaw table to see if same URL with same hash exists.
        
        Args:
            db: Database session.
            url: URL that was fetched.
            hash_contenido: SHA-256 hash of the content.
            
        Returns:
            True if this is new content (never seen this URL+hash before).
        """
        from scraper.db.models import DocumentoRaw
        from sqlalchemy import select
        
        stmt = select(DocumentoRaw).where(
            DocumentoRaw.url == url,
            DocumentoRaw.hash_contenido == hash_contenido,
        )
        result = await db.execute(stmt)
        return result.first() is None  # True if no match found (new content)
    
    async def save_raw(
        self,
        db: AsyncSession,
        content: bytes,
        fuente: str,
        url: str,
        hash_contenido: str,
        meta_info: Optional[dict] = None,
    ) -> UUID:
        """Persist raw content to Bronze layer (DocumentoRaw table).
        
        Args:
            db: Database session.
            content: Raw bytes content.
            fuente: Source identifier (e.g., 'datos_abiertos', 'simce', 'sige').
            url: URL where content was fetched from.
            hash_contenido: SHA-256 hash of the content.
            meta_info: Optional metadata dictionary (stored as JSON).
            
        Returns:
            UUID of created DocumentoRaw record.
        """
        import json
        from scraper.db.models import DocumentoRaw
        
        doc = DocumentoRaw(
            fuente=fuente,
            url=url,
            hash_contenido=hash_contenido,
            contenido=content.decode('utf-8', errors='replace'),
            meta_info=json.dumps(meta_info or {}),
        )
        db.add(doc)
        await db.flush()
        
        logger.info(f"Saved raw document: fuente={fuente}, url={url}, id={doc.id}")
        return doc.id
    
    @abstractmethod
    async def extract(self, url: str) -> dict:
        """Extract structured data from URL. Must be implemented by subclasses.
        
        Args:
            url: URL to extract data from.
            
        Returns:
            Extracted data as dictionary. Must include 'rbd' field.
        """
        pass
    
    async def close(self) -> None:
        """Close HTTP client if owned by this extractor."""
        if self._should_close_client and self.client is not None:
            await self.client.aclose()
