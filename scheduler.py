"""
Планувальник задач - запускається разом з ботами.
Щодня перевіряє бали з терміном дії.
"""

import asyncio
import logging
from datetime import datetime
from database import expire_points

logger = logging.getLogger(__name__)


async def daily_expire_job():
    """Щодня о 3:00 анулювати протерміновані бали."""
    while True:
        now = datetime.utcnow()
        # Наступний запуск о 3:00 UTC
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run = next_run.replace(day=now.day + 1)

        wait_seconds = (next_run - now).total_seconds()
        logger.info(f"Наступна перевірка балів через {wait_seconds / 3600:.1f} годин")
        await asyncio.sleep(wait_seconds)

        try:
            count = await expire_points()
            if count:
                logger.info(f"Анульовано бали у {count} гостей.")
        except Exception as e:
            logger.error(f"Помилка при анулюванні балів: {e}")
