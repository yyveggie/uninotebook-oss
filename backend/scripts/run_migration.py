#!/usr/bin/env python3
"""
执行数据库SQL迁移脚本
"""
import sys
import os
from pathlib import Path
from database import engine
from sqlalchemy import text
from utils.logger import get_logger
logger = get_logger(__name__)

def run_sql_migration(sql_file_path: str):
    """
    执行SQL迁移文件
    
    Args:
        sql_file_path: SQL文件路径
    """
    sql_file = Path(sql_file_path)
    
    if not sql_file.exists():
        logger.error(f"❌ SQL文件不存在: {sql_file_path}")
        return False
    
    logger.info(f"\n{'='*60}")
    logger.info(f"🔄 执行数据库迁移")
    logger.info(f"{'='*60}")
    logger.info(f"📄 SQL文件: {sql_file.name}")
    logger.info(f"📊 数据库: {engine.url}")
    logger.info(f"{'='*60}\n")
    
    try:
        # 读取SQL文件
        with open(sql_file, 'r', encoding='utf-8') as f:
            sql_content = f.read()
        
        # 移除注释行和验证查询部分（我们只执行ALTER语句）
        sql_statements = []
        current_statement = []
        
        for line in sql_content.split('\n'):
            # 跳过注释行
            if line.strip().startswith('--') or not line.strip():
                continue
            
            # 跳过验证查询（SELECT语句在最后）
            if line.strip().upper().startswith('SELECT'):
                break
            
            current_statement.append(line)
            
            # 如果遇到分号，说明语句结束
            if ';' in line:
                statement = '\n'.join(current_statement).strip()
                if statement:
                    sql_statements.append(statement)
                current_statement = []
        
        # 执行所有语句
        with engine.connect() as conn:
            for i, statement in enumerate(sql_statements, 1):
                try:
                    logger.info(f"🔄 执行语句 {i}/{len(sql_statements)}...")
                    
                    # 显示语句类型
                    if 'DROP CONSTRAINT' in statement.upper():
                        logger.info(f"   ↳ 删除旧约束")
                    elif 'ADD CONSTRAINT' in statement.upper():
                        # 提取表名
                        table_name = statement.split()[2] if len(statement.split()) > 2 else "unknown"
                        logger.info(f"   ↳ 添加级联删除约束到 {table_name}")
                    
                    # 执行语句（使用text()包装）
                    conn.execute(text(statement))
                    conn.commit()
                    logger.info(f"   ✅ 成功")
                    
                except Exception as e:
                    error_msg = str(e)
                    # 如果是"约束不存在"的错误，可以忽略
                    if 'does not exist' in error_msg.lower():
                        logger.warning(f"   ⚠️ 约束不存在（已跳过）")
                    else:
                        logger.error(f"   ❌ 失败: {error_msg}")
                        # 对于其他错误，继续执行
                        continue
        
        logger.info(f"\n{'='*60}")
        logger.info(f"✅ 数据库迁移完成！")
        logger.info(f"{'='*60}")
        logger.info(f"")
        logger.info(f"📋 迁移效果：")
        logger.info(f"   • 删除文档时，将自动级联删除：")
        logger.info(f"     - 聊天历史记录")
        logger.info(f"     - 学习路径记录")
        logger.info(f"     - 闪卡记录")
        logger.info(f"     - 测验记录")
        logger.info(f"     - 相关笔记")
        logger.info(f"   • 向量数据库的collection也会被清理")
        logger.info(f"   • 有效释放存储空间")
        logger.info(f"")
        
        # 验证外键约束
        logger.info(f"🔍 验证外键约束...")
        with engine.connect() as conn:
            verify_sql = """
            SELECT 
                tc.table_name,
                rc.delete_rule
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
                ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage AS ccu
                ON ccu.constraint_name = tc.constraint_name
            JOIN information_schema.referential_constraints AS rc
                ON rc.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
                AND ccu.table_name = 'documents'
            ORDER BY tc.table_name;
            """
            result = conn.execute(text(verify_sql))
            rows = result.fetchall()
            
            if rows:
                logger.info(f"")
                logger.info(f"📊 外键约束状态：")
                for row in rows:
                    table_name = row[0]
                    delete_rule = row[1]
                    emoji = "✅" if delete_rule == "CASCADE" else "⚠️"
                    logger.info(f"   {emoji} {table_name}: {delete_rule}")
            
        logger.info(f"")
        return True
        
    except Exception as e:
        logger.info(f"\n❌ 迁移失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主函数"""
    # 默认迁移文件
    default_migration = "migrations/add_cascade_delete_to_document_relations.sql"
    
    # 如果提供了命令行参数，使用指定的文件
    sql_file = sys.argv[1] if len(sys.argv) > 1 else default_migration
    
    # 确保路径正确
    if not os.path.isabs(sql_file):
        # 相对于当前脚本的路径
        script_dir = Path(__file__).parent
        sql_file = script_dir / sql_file
    
    success = run_sql_migration(str(sql_file))
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()