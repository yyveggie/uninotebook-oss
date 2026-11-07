"""
工业级分布式限流器
支持滑动窗口、用户级别限流、IP限流
"""

import time
import asyncio
import ipaddress
import uuid
from typing import Optional, Dict, Tuple
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from cache_service import get_redis_client
from utils.logger import get_logger
logger = get_logger(__name__)

# 受信反向代理所在网段：本机 + 内网（Nginx 与后端同机 / 同内网部署）。
# 不用 ipaddress 的 is_private——它还包含文档网段、Teredo 等并非「自己人」的地址。
_TRUSTED_PROXY_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in ("127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


class DistributedRateLimiter:
    """分布式滑动窗口限流器（基于Redis）"""
    
    def __init__(self):
        self.fallback_cache: Dict[str, list] = {}  # Redis不可用时的降级缓存
    
    async def check_rate_limit(
        self,
        key: str,
        limit: int,
        window: int = 60
    ) -> Tuple[bool, int, int]:
        """
        检查是否超过速率限制
        
        Args:
            key: 限流键（如 user:123 或 ip:1.2.3.4）
            limit: 限制次数
            window: 时间窗口（秒）
            
        Returns:
            (是否允许, 剩余次数, 重置时间)
        """
        now = time.time()
        redis_client = await get_redis_client()
        
        # Redis可用：使用分布式滑动窗口
        if redis_client:
            try:
                pipe = redis_client.pipeline()
                member_id = uuid.uuid4().hex
                
                # 1. 添加当前时间戳
                pipe.zadd(key, {member_id: now})
                
                # 2. 删除窗口外的记录
                pipe.zremrangebyscore(key, 0, now - window)
                
                # 3. 统计窗口内的请求数
                pipe.zcard(key)
                
                # 4. 设置key过期时间
                pipe.expire(key, window + 10)
                
                results = await pipe.execute()
                request_count = results[2]
                
                remaining = max(0, limit - request_count)
                reset_time = int(now + window)
                
                allowed = request_count <= limit
                
                if not allowed:
                    # 超限，删除刚才添加的记录member_id
                    await redis_client.zrem(key, member_id)
                
                return allowed, remaining, reset_time
                
            except Exception as e:
                logger.warning(f"⚠️ Redis限流失败，降级到内存限流: {e}")
                # 降级到内存缓存
                pass
        
        # Redis不可用：使用内存滑动窗口（单实例）
        if key not in self.fallback_cache:
            self.fallback_cache[key] = []
        
        timestamps = self.fallback_cache[key]
        
        # 清理过期记录
        timestamps[:] = [t for t in timestamps if t > now - window]
        
        # 检查限制
        allowed = len(timestamps) < limit
        
        if allowed:
            timestamps.append(now)
        
        remaining = max(0, limit - len(timestamps))
        reset_time = int(now + window)
        
        return allowed, remaining, reset_time


    async def check_rate_limits_batch(
        self,
        checks: list,
    ) -> list:
        """
        批量限流检查（单次 Redis pipeline 完成所有层级）

        Args:
            checks: [(name, key, rule_dict), ...]  rule_dict = {"limit": int, "window": int}

        Returns:
            [(allowed, remaining, reset_time), ...] 与 checks 一一对应
        """
        if not checks:
            return []

        now = time.time()
        redis_client = await get_redis_client()

        if redis_client:
            try:
                pipe = redis_client.pipeline()
                member_ids = []

                for _, key, rule in checks:
                    member_id = uuid.uuid4().hex
                    member_ids.append(member_id)
                    window = rule["window"]
                    pipe.zadd(key, {member_id: now})
                    pipe.zremrangebyscore(key, 0, now - window)
                    pipe.zcard(key)
                    pipe.expire(key, window + 10)

                raw_results = await pipe.execute()

                results = []
                for i, (_, key, rule) in enumerate(checks):
                    count = raw_results[i * 4 + 2]
                    limit = rule["limit"]
                    window = rule["window"]
                    allowed = count <= limit
                    remaining = max(0, limit - count)
                    reset_time = int(now + window)

                    if not allowed:
                        await redis_client.zrem(key, member_ids[i])

                    results.append((allowed, remaining, reset_time))

                return results
            except Exception as e:
                logger.warning(f"⚠️ Redis批量限流失败，降级到内存: {e}")

        # Redis 不可用：逐个用内存限流
        results = []
        for _, key, rule in checks:
            allowed, remaining, reset_time = await self.check_rate_limit(
                key, rule["limit"], rule["window"]
            )
            results.append((allowed, remaining, reset_time))
        return results


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    速率限制中间件（工业级）
    
    限流规则：
    - 文档上传：10次/分钟/用户
    - 问答请求：30次/分钟/用户
    - 生成功能：20次/分钟/用户
    - 全局：200次/分钟/IP
    - 认证接口（发验证码 / 校验验证码 / 登录）：按 IP 分钟级 + 小时级双窗口
    """
    
    def __init__(self, app):
        super().__init__(app)
        self.limiter = DistributedRateLimiter()
        
        # 限流规则配置（优化：提高限制，避免正常使用触发）
        self.rules = {
            "upload": {"limit": 20, "window": 60},        # 20次/分钟
            "ask": {"limit": 60, "window": 60},           # 60次/分钟
            "generate": {"limit": 30, "window": 60},      # 30次/分钟
            "global_ip": {"limit": 600, "window": 60},    # 600次/分钟/IP（提高3倍）
            "global_user": {"limit": 400, "window": 60},  # 400次/分钟/用户（提高2.5倍）
        }
        
        # 白名单路径（不限流）
        self.whitelist_paths = {
            "/api/document/", "/api/history/", "/api/models",
            "/api/knowledge-graph/", "/api/auth/"
        }

        # 认证接口按 IP 限流。白名单只对 GET 生效，这些 POST 接口此前只受全局 IP 限流，
        # 远不足以防范短信/邮件轰炸与撞库。认证请求多为匿名，只能按 IP 计数，
        # 阈值兼顾校园网等 NAT 共享出口；/api/auth/refresh、/api/auth/logout 不在此列。
        self.auth_rules = {
            "auth_send_code": [{"limit": 5, "window": 60}, {"limit": 30, "window": 3600}],   # 发送验证码
            "auth_verify": [{"limit": 10, "window": 60}, {"limit": 100, "window": 3600}],    # 注册/重置密码/绑定（校验验证码）
            "auth_login": [{"limit": 20, "window": 60}, {"limit": 200, "window": 3600}],     # 密码登录
        }
        self.auth_path_rules = {
            "/api/auth/send-verification-code": "auth_send_code",
            "/api/auth/send-password-reset-code": "auth_send_code",
            "/api/auth/send-sms-code": "auth_send_code",
            "/api/auth/send-bind-email-code": "auth_send_code",
            "/api/auth/send-bind-phone-code": "auth_send_code",
            "/api/auth/register": "auth_verify",
            "/api/auth/reset-password": "auth_verify",
            "/api/auth/bind-email": "auth_verify",
            "/api/auth/bind-phone": "auth_verify",
            "/api/auth/login": "auth_login",
        }
    
    @staticmethod
    def _is_trusted_proxy(host: Optional[str]) -> bool:
        """直连方是否为受信反向代理（本机 / 内网的 Nginx）"""
        if not host:
            return False
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            return False
        if getattr(addr, "ipv4_mapped", None):
            addr = addr.ipv4_mapped
        return any(addr in net for net in _TRUSTED_PROXY_NETWORKS)

    def _get_client_ip(self, request: Request) -> str:
        """
        获取客户端IP（支持代理）

        X-Forwarded-For 的首个值可由客户端任意伪造（Nginx 的 $proxy_add_x_forwarded_for 只在末尾追加），
        不能用来限流。仅当直连方是受信反代时才采信代理头：优先 X-Real-IP（Nginx 设为 $remote_addr），
        其次取 X-Forwarded-For 的最后一跳；否则直接用连接 IP——防止绕过 Nginx 直连后端端口伪造代理头。
        """
        peer = request.client.host if request.client else None
        
        if self._is_trusted_proxy(peer):
            real_ip = (request.headers.get("X-Real-IP") or "").strip()
            if real_ip:
                return real_ip

            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
                if hops:
                    return hops[-1]
        
        # 最后使用直接连接IP
        return peer or "unknown"
    
    def _get_user_id(self, request: Request) -> Optional[str]:
        """从请求中提取用户ID（从token）"""
        try:
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return None
            
            token = auth_header.replace("Bearer ", "")
            
            # 简单解析JWT（不验证，只提取user_id）
            import base64
            import json
            payload = token.split('.')[1]
            # 补齐padding
            payload += '=' * (4 - len(payload) % 4)
            decoded = base64.b64decode(payload)
            data = json.loads(decoded)
            
            return str(data.get("sub"))  # user_id
        except Exception:
            return None
    
    def _match_rule(self, path: str) -> Optional[str]:
        """匹配限流规则"""
        if "/api/document/upload" in path:
            return "upload"
        elif "/ask" in path or "/qa" in path:
            return "ask"
        elif "/generate" in path:
            return "generate"
        return None

    def _match_auth_rule(self, path: str) -> Optional[str]:
        """匹配认证接口限流规则（精确匹配路径）"""
        return self.auth_path_rules.get(path.rstrip("/"))
    
    async def dispatch(self, request: Request, call_next):
        """中间件主逻辑"""
        # 忽略 OPTIONS 请求，由 CORS 中间件处理
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        method = request.method
        
        # 健康检查端点不限流
        if path in ["/health", "/api/health", "/docs", "/openapi.json"]:
            return await call_next(request)
        
        # GET 请求对白名单路径不做全局限流（只做端点级限流）
        if method == "GET":
            for whitelist_path in self.whitelist_paths:
                if path.startswith(whitelist_path):
                    return await call_next(request)
        
        client_ip = self._get_client_ip(request)
        user_id = self._get_user_id(request)
        
        # 批量限流检查（合并所有层级到单次 Redis pipeline）
        checks = [("ip", f"ratelimit:ip:{client_ip}", self.rules["global_ip"])]

        if user_id and path.startswith("/api/"):
            checks.append(("user", f"ratelimit:user:{user_id}", self.rules["global_user"]))

        rule = self._match_rule(path)
        if rule and user_id:
            checks.append(("endpoint", f"ratelimit:user:{user_id}:{rule}", self.rules[rule]))

        # 认证接口：按 IP 的分钟级 + 小时级限流
        auth_group = self._match_auth_rule(path) if method == "POST" else None
        if auth_group:
            for auth_rule in self.auth_rules[auth_group]:
                checks.append(("auth", f"ratelimit:ip:{client_ip}:{auth_group}:{auth_rule['window']}", auth_rule))

        results = await self.limiter.check_rate_limits_batch(checks)

        for check_name, check_rule, (allowed, remaining, reset_time) in zip(
            [c[0] for c in checks], [c[2] for c in checks], results
        ):
            if not allowed:
                error_messages = {
                    "ip": "请求过于频繁，请稍后再试",
                    "user": "您的请求过于频繁，请稍后再试",
                    "endpoint": "该操作过于频繁，请稍后再试",
                    "auth": "操作过于频繁，请稍后再试",
                }
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": error_messages.get(check_name, "请求过于频繁"),
                        "limit": check_rule["limit"],
                        "remaining": 0,
                        "reset": reset_time,
                    },
                    headers={
                        "X-RateLimit-Limit": str(check_rule["limit"]),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(reset_time),
                        "Retry-After": str(60),
                    },
                )

        ip_remaining = results[0][1] if results else 0

        # 执行请求
        response = await call_next(request)
        
        # 添加限流信息头
        response.headers["X-RateLimit-Limit-IP"] = str(self.rules["global_ip"]["limit"])
        response.headers["X-RateLimit-Remaining-IP"] = str(ip_remaining)
        
        return response


# 全局限流器实例
rate_limiter = RateLimitMiddleware

