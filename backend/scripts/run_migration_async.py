#!/usr/bin/env python3
"""
运行异步处理迁移脚本
"""
import asyncio
from sqlalchemy import text
from database import async_engine
from utils.logger import get_logger
logger = get_logger(__name__)

async def run_migration():
    """执行迁移"""
    logger.info("🔄 开始执行数据库迁移...")
    
    try:
        # 读取迁移脚本
        with open('migrations/add_async_processing_status.sql', 'r') as f:
            migration_sql = f.read()
        
        # 执行迁移
        async with async_engine.begin() as conn:
            # 分割SQL语句（按分号）
            statements = [s.strip() for s in migration_sql.split(';') if s.strip()]
            
            for i, statement in enumerate(statements, 1):
                # 跳过注释行
                if statement.startswith('--') or statement.startswith('COMMENT'):
                    continue
                
                try:
                    logger.info(f"执行语句 {i}/{len(statements)}...")
                    await conn.execute(text(statement))
                    logger.info(f"  ✅ 成功")
                except Exception as e:
                    # 如果是字段已存在的错误，忽略
                    if 'already exists' in str(e).lower():
                        logger.warning(f"  ⚠️ 跳过（字段已存在）")
                    else:
                        logger.error(f"  ❌ 失败: {e}")
                        raise
        
        logger.info("✅ 迁移完成！")
        logger.info("\n数据库已更新，新增字段：")
        logger.info("  - processing_status (VARCHAR)")
        logger.info("  - processing_error (TEXT)")
        logger.info("  - processing_progress (INTEGER)")
        
    except Exception as e:
        logger.error(f"❌ 迁移失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    result = asyncio.run(run_migration())
    exit(0 if result else 1)

