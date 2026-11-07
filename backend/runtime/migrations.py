"""Non-destructive SQLite schema upgrades with a consistent pre-upgrade backup."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone

from sqlalchemy import inspect
from sqlalchemy.schema import CreateColumn

from config import settings

SCHEMA_VERSION = 1


def _backup_database():
    path = settings.data_dir / "uninotebook.db"
    if not path.exists() or not path.stat().st_size:
        return
    folder = settings.data_dir / "backups"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = folder / f"before-upgrade-{stamp}.db"
    target.touch(mode=0o600)
    with sqlite3.connect(path) as source, sqlite3.connect(target) as backup:
        source.backup(backup)
    target.chmod(0o600)


async def upgrade_local_database(engine, metadata):
    def plan(connection):
        inspector = inspect(connection)
        existing = set(inspector.get_table_names())
        version = 0
        if "uninotebook_schema" in existing:
            version = connection.exec_driver_sql(
                "SELECT COALESCE(MAX(version), 0) FROM uninotebook_schema"
            ).scalar()
        if version > SCHEMA_VERSION:
            raise RuntimeError("数据库版本比当前程序新，请使用较新版本启动；未修改任何数据")
        additions = []
        for table in metadata.sorted_tables:
            if table.name not in existing:
                continue
            present = {c["name"]: c for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    if present[column.name]["type"]._type_affinity != column.type._type_affinity:
                        raise RuntimeError(
                            f"无法自动升级 {table.name}.{column.name}：字段类型变化需要显式迁移，原数据未修改"
                        )
                    continue
                definition = str(CreateColumn(column).compile(dialect=connection.dialect))
                if not column.nullable and column.server_default is None:
                    default = column.default
                    if default is not None and default.is_scalar:
                        literal = connection.dialect.statement_compiler(
                            connection.dialect, None
                        ).render_literal_value(default.arg, column.type)
                        definition += f" DEFAULT {literal}"
                    else:
                        count = connection.exec_driver_sql(
                            f'SELECT COUNT(*) FROM "{table.name}"'
                        ).scalar()
                        if count:
                            raise RuntimeError(
                                f"无法自动升级 {table.name}.{column.name}：新增必填列需要显式迁移，原数据未修改"
                            )
                additions.append(f'ALTER TABLE "{table.name}" ADD COLUMN {definition}')
        missing_tables = set(metadata.tables) - existing
        return bool(existing - {"uninotebook_schema"}) and bool(additions or missing_tables), additions

    async with engine.connect() as connection:
        needs_backup, additions = await connection.run_sync(plan)
    if needs_backup:
        await asyncio.to_thread(_backup_database)
    async with engine.begin() as connection:
        await connection.exec_driver_sql("BEGIN IMMEDIATE")
        for statement in additions:
            await connection.exec_driver_sql(statement)
        await connection.run_sync(metadata.create_all)
        await connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS uninotebook_schema (version INTEGER PRIMARY KEY)"
        )
        await connection.exec_driver_sql(
            "INSERT OR IGNORE INTO uninotebook_schema(version) VALUES (?)", (SCHEMA_VERSION,)
        )
