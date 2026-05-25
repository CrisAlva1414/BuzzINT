"""Encryption utilities for sensitive credentials."""
from cryptography.fernet import Fernet
from scraper.core.config import settings


def encrypt_credentials(plaintext: str) -> str:
    """Encrypt plaintext credentials using Fernet.
    
    Args:
        plaintext: Plaintext credential string.
        
    Returns:
        Encrypted token string.
    """
    cipher = Fernet(settings.fernet_key.encode())
    return cipher.encrypt(plaintext.encode()).decode()


def decrypt_credentials(encrypted: str) -> str:
    """Decrypt encrypted credentials.
    
    Args:
        encrypted: Encrypted credential string.
        
    Returns:
        Decrypted plaintext.
    """
    cipher = Fernet(settings.fernet_key.encode())
    return cipher.decrypt(encrypted.encode()).decode()
