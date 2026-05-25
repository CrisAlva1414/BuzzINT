"""Scheduler jobs module."""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


async def setup_scheduler() -> AsyncIOScheduler:
    """Setup and start APScheduler."""
    scheduler = AsyncIOScheduler()
    
    # Add jobs here
    # scheduler.add_job(job_function, "cron", hour=0, minute=0)
    
    logger.info("Scheduler configured")
    return scheduler
