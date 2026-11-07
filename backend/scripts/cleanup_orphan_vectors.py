"""
手动清理孤立向量集合（文档入库中途被中断遗留，不再被任何文档或来源引用）

日常无需手动执行：Celery worker 子进程启动时、本地版启动时都会自动清理
6 小时前创建的孤立集合。本脚本用于查看情况，或一次性清理早期版本遗留的、
没有创建时间的集合。

用法（在 backend/ 目录下；本地版请先设置 DEPLOY_MODE=local）：
    python scripts/cleanup_orphan_vectors.py --dry-run          # 只列出，不删除
    python scripts/cleanup_orphan_vectors.py                    # 删除 6 小时前创建的孤立集合
    python scripts/cleanup_orphan_vectors.py --include-legacy   # 连同没有创建时间的旧集合一起删除

--include-legacy 前请确认所有后端 / worker 进程都已升级到当前版本：旧版本进程创建的
集合没有创建时间，若它正在写入，会被误删。
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import AsyncSessionLocal  # noqa: E402
from document_service.vector_maintenance import (  # noqa: E402
    ORPHAN_MIN_AGE_SECONDS,
    sweep_orphan_collections,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="清理孤立向量集合")
    parser.add_argument("--dry-run", action="store_true", help="只列出，不删除")
    parser.add_argument("--include-legacy", action="store_true", help="包括没有创建时间的旧集合")
    parser.add_argument(
        "--min-age-hours", type=float, default=ORPHAN_MIN_AGE_SECONDS / 3600,
        help="只处理创建时间早于该小时数的集合（默认 6）",
    )
    args = parser.parse_args()

    orphans = asyncio.run(sweep_orphan_collections(
        AsyncSessionLocal,
        min_age_seconds=args.min_age_hours * 3600,
        include_legacy=args.include_legacy,
        dry_run=args.dry_run,
    ))
    action = "将删除" if args.dry_run else "已删除"
    print(f"{action} {len(orphans)} 个孤立向量集合")
    for name in orphans:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
