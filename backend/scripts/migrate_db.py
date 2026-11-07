"""数据库迁移脚本（异步）
用于创建或更新数据库表结构。

直接复用 ``database.init_db``，避免在同步上下文中误用 asyncpg。
"""

import sys
import asyncio

from database import async_engine, init_db
from utils.logger import get_logger
logger = get_logger(__name__)


async def migrate() -> bool:
    """执行数据库迁移（调用 init_db 创建所有表）。"""
    try:
        logger.info("🔄 开始数据库迁移...")
        logger.info(f"📊 目标数据库: {async_engine.url}")

        # init_db 会导入 db_models 和 knowledge_graph.models 并创建所有表
        await init_db()

        # 仅用于展示当前已注册的表名
        from db_models import Base  # noqa: F401

        logger.info("✅ 数据库迁移成功！")
        logger.info("\n📋 已创建/更新的表：")
        for table_name in Base.metadata.tables.keys():
            logger.info(f"   - {table_name}")

        return True
    except Exception as e:
        logger.error(f"❌ 数据库迁移失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(migrate())
    sys.exit(0 if success else 1)

