#!/usr/bin/env python3
"""
UniNotebook 本地版启动器

一条命令跑起来，不需要 PostgreSQL、Redis、Celery 或独立的 ChromaDB 服务：

    python run.py

数据默认落在 `~/.uninotebook/`（可用 --data-dir 或 LOCAL_DATA_DIR 改）：

    ~/.uninotebook/
    ├── uninotebook.db      SQLite 数据库
    ├── credentials.json    你在设置页填写的 API Key（权限 0600）
    ├── chroma_db/          向量库
    └── data/               上传的文档与生成产物

不需要注册或登录。首次启动会自动构建前端（需要 Node.js），随后浏览器打开
http://127.0.0.1:8000 直接进入工作台；用到 AI 功能时在设置页填入模型的 API Key 即可。
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"

# 本地版免登录：只有监听这些地址时，服务才只对本机可见
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def check_dependencies() -> None:
    """启动前检查关键依赖，缺失时给出可直接执行的修复命令"""
    missing = []
    for module, hint in (
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn[standard]"),
        ("sqlalchemy", "sqlalchemy"),
        ("aiosqlite", "aiosqlite"),
        ("chromadb", "chromadb"),
        ("langchain_openai", "langchain-openai"),
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(hint)

    if missing:
        # 依赖清单可能叫 requirements-local.txt 或 requirements.txt，两者都支持。
        root = Path(__file__).resolve().parent
        req = "requirements-local.txt" if (root / "requirements-local.txt").exists() else "requirements.txt"
        print("❌ 缺少依赖：" + ", ".join(missing))
        print("\n请先安装：")
        print(f"    pip install -r {req}")
        sys.exit(1)


def build_frontend() -> bool:
    """
    首次启动时自动构建前端，免去用户手动执行 npm 命令。

    没有 Node.js 时返回 False，由调用方给出手动构建指引。
    """
    npm = shutil.which("npm")
    if not npm or not (FRONTEND_DIR / "package.json").exists():
        return False

    print("  📦 首次启动：正在构建前端界面（约 1～2 分钟，仅需一次）…")
    env = {**os.environ, "VITE_DEPLOY_MODE": "local"}
    try:
        if not (FRONTEND_DIR / "node_modules").exists():
            install = "ci" if (FRONTEND_DIR / "package-lock.json").exists() else "install"
            subprocess.run([npm, install], cwd=FRONTEND_DIR, env=env, check=True)
        subprocess.run([npm, "run", "build"], cwd=FRONTEND_DIR, env=env, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"  ❌ 前端构建失败：{exc}")
        return False
    return (FRONTEND_DIR / "dist" / "index.html").exists()


def open_browser_when_ready(url: str, host: str, port: int) -> None:
    """服务真正开始监听后再打开浏览器，避免首屏打开时连接被拒绝"""

    def wait_and_open() -> None:
        probe_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((probe_host, port), timeout=1):
                    break
            except OSError:
                time.sleep(0.3)
        else:
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=wait_and_open, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="启动 UniNotebook 本地版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument("--data-dir", help="数据目录（默认 ~/.uninotebook）")
    parser.add_argument("--reload", action="store_true", help="代码变更自动重载（开发用）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--no-build", action="store_true", help="前端未构建时不自动构建")
    args = parser.parse_args()

    # 必须在导入任何后端模块之前设置——config 在导入时即读取环境变量
    os.environ["DEPLOY_MODE"] = "local"
    # 让设置页「运行环境」显示实际监听地址，而不是配置里的默认值
    os.environ["API_HOST"] = args.host
    os.environ["API_PORT"] = str(args.port)
    if args.data_dir:
        os.environ["LOCAL_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())

    # 后端代码使用扁平导入（from config import settings），需把 backend/ 加入路径
    sys.path.insert(0, str(BACKEND_DIR))
    os.chdir(BACKEND_DIR)

    check_dependencies()

    import uvicorn
    from config import settings

    data_dir = settings.data_dir
    # 数据目录里是你的文档、对话与 API Key，仅允许当前系统用户访问
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    frontend_built = (FRONTEND_DIR / "dist" / "index.html").exists()
    if not frontend_built and not args.no_build:
        frontend_built = build_frontend()

    # 监听 0.0.0.0 时浏览器仍应走回环地址打开
    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{display_host}:{args.port}"

    print("━" * 52)
    print("  UniNotebook · 本地版")
    print("━" * 52)
    print(f"  数据目录   {data_dir}")
    print(f"  访问地址   {url}")
    print(f"  接口文档   {url}/docs")
    print("━" * 52)
    if args.host not in LOOPBACK_HOSTS:
        print()
        print(f"  ⚠️ 正在监听 {args.host}：本地版没有登录保护，同一网络中的任何人")
        print("     都能查看你的文档、使用你配置的 API Key。请只在可信网络中这样做。")
    if not frontend_built:
        print()
        print("  ⚠️ 尚未构建前端，当前只有 API 可用。安装 Node.js 后重新运行，或手动执行：")
        print("       cd frontend && npm install && npm run build")
    print()

    if not args.no_browser and not args.reload and frontend_built:
        # reload 模式下进程会重启，自动开浏览器会重复弹窗
        open_browser_when_ready(url, args.host, args.port)

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
