
import asyncio
import sys
import os
from utils.logger import get_logger
logger = get_logger(__name__)

# Add backend directory to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from database import async_engine
from sqlalchemy import text

async def patch_table():
    async with async_engine.begin() as conn:
        logger.info("Adding columns to document_ppts...")
        try:
            await conn.execute(text("ALTER TABLE document_ppts ADD COLUMN IF NOT EXISTS session_id VARCHAR(100);"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_document_ppts_session_id ON document_ppts (session_id);"))
            await conn.execute(text("ALTER TABLE document_ppts ADD COLUMN IF NOT EXISTS source_file_path VARCHAR(500);"))
            await conn.execute(text("ALTER TABLE document_ppts ADD COLUMN IF NOT EXISTS file_size INTEGER DEFAULT 0;"))
            logger.info("Success!")
        except Exception as e:
            logger.info(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(patch_table())
