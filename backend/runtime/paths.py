"""
数据路径解析

各处直接写 `Path("data/xxx")` 时，解析结果取决于进程的当前工作目录。
服务端部署固定在 backend/ 下启动，因此没问题；但本地部署需要把用户数据
统一落到 `~/.uninotebook/`，而不是散落在用户克隆的代码目录里。

用法：把 `Path("data/ppt_output")` 换成 `data_path("ppt_output")`。
非本地模式下仍解析为 `./data/ppt_output`。
"""
from __future__ import annotations

from pathlib import Path

from config import settings


def data_root() -> Path:
    """用户数据根目录"""
    if settings.is_local_mode:
        return settings.data_dir / "data"
    return Path("data")


def data_path(*parts: str) -> Path:
    """拼接数据目录下的路径（不自动创建）"""
    return data_root().joinpath(*parts)


def ensure_data_path(*parts: str) -> Path:
    """拼接并确保目录存在"""
    path = data_path(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path
