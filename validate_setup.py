#!/usr/bin/env python
"""Quick validation script to verify project setup."""

try:
    from scraper.core.config import settings
    print("✓ Config loaded")
    
    from scraper.db.models import Base
    print("✓ Models loaded")
    
    from scraper.api.main import app
    print("✓ API loaded")
    
    from scraper.extractors.base import BaseExtractor
    print("✓ Extractors base loaded")
    
    from scraper.normalizers.base import BaseNormalizer
    print("✓ Normalizers base loaded")
    
    print("\n✅ All imports successful!")
    print("✅ Project is ready for development!")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
