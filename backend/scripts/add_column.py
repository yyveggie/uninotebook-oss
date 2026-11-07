import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal
from utils.logger import get_logger
logger = get_logger(__name__)

async def add_column():
    async with AsyncSessionLocal() as session:
        try:
            # Check if column exists first to avoid error
            logger.info("🔧 Checking 'plan' column in document_chat_history...")
            await session.execute(text("ALTER TABLE document_chat_history ADD COLUMN IF NOT EXISTS plan JSON;"))
            await session.commit()
            logger.info("✅ Successfully added 'plan' column.")
        except Exception as e:
            logger.error(f"❌ Error adding column: {e}")
            await session.rollback()

if __name__ == "__main__":
    asyncio.run(add_column())
