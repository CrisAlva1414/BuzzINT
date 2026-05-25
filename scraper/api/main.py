"""FastAPI main application."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

logger = logging.getLogger(__name__)

app = FastAPI(
    title="MINEDUC Intelligence API",
    description="Educational data scraping and analysis platform",
    version="0.1.0",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/")
async def root() -> dict:
    """Root endpoint."""
    return {
        "name": "MINEDUC Intelligence API",
        "version": "0.1.0",
        "docs": "/docs",
    }


# Import routers here when created
# from scraper.api.routers import director, sources, jobs
# app.include_router(director.router)
# app.include_router(sources.router)
# app.include_router(jobs.router)


if __name__ == "__main__":
    import uvicorn
    from scraper.core.config import settings
    
    uvicorn.run(
        "scraper.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
    )
