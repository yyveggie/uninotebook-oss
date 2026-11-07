"""
运行时抽象层

为「单用户本地部署」与「多用户服务端部署」提供统一接缝，使两种部署共享同一份核心代码：

- credentials : 运行时凭据（用户自填 API Key）↔ 环境变量
- identity    : 固定单用户 ↔ 真实 JWT 鉴权
- billing     : 无额度扣减 ↔ 额度服务
- tasks       : 进程内后台任务 ↔ Celery 分布式任务
- features    : 能力探测（缺 Key / 缺可选依赖时优雅降级）
- database    : SQLite ↔ PostgreSQL
- paths       : 数据目录解析

设计约束：非本地模式下所有抽象一律回落到原有实现，行为保持不变。
"""
from config import settings


def is_local_mode() -> bool:
    """当前是否为单用户本地部署"""
    return settings.is_local_mode


def is_saas_mode() -> bool:
    """当前是否为多用户服务端部署（默认）"""
    return settings.is_saas_mode


__all__ = ["is_local_mode", "is_saas_mode"]
