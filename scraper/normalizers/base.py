"""Base normalizer abstract class."""
from abc import ABC, abstractmethod
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class BaseNormalizer(ABC):
    """Abstract base class for data normalizers."""
    
    @abstractmethod
    async def normalize(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize raw extracted data.
        
        Args:
            raw_data: Raw extracted data dictionary.
            
        Returns:
            Normalized data dictionary with resolved RBD.
        """
        pass
    
    def validate_rbd(self, rbd: str) -> bool:
        """Validate RBD format.
        
        Args:
            rbd: RBD identifier.
            
        Returns:
            True if RBD is valid.
        """
        # RBD should be numeric, typically 5-10 digits
        return bool(rbd) and rbd.isdigit() and 5 <= len(rbd) <= 10
