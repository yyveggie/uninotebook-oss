"""
本地版访问守卫

本地版免登录：任何能把请求送到本机端口的人都等同于本机用户。而用户浏览器里打开的
任意网页恰恰能做到这一点，因此需要两道闸门（只在本地模式安装）：

1. **Host 校验**（防 DNS 重绑定）：恶意域名把自己解析到 127.0.0.1 后，浏览器会把它
   视为同源页面，能完整读写本地接口——读文档、改设置页里的模型端点。此类请求的
   Host 头是攻击者的域名，因此只放行 localhost、IP 字面量，以及
   `LOCAL_ALLOWED_HOSTS` 中显式列出的主机名（填 `*` 可关闭此项校验）。
2. **来源校验**（防 CSRF）：跨站页面无需预检就能提交表单、上传文件。写操作若带
   Origin，必须与当前站点同源或在 CORS 白名单内；不带 Origin 但浏览器标注了
   `Sec-Fetch-Site: cross-site` 的同样拒绝。命令行工具不带这些头，不受影响。
"""
from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit

from config import settings

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _hostname(host_header: str) -> str:
    """从 Host 头取出主机名（去端口、去 IPv6 方括号、转小写）"""
    value = (host_header or "").strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end != -1 else ""
    if value.count(":") == 1:
        value = value.split(":", 1)[0]
    return value.rstrip(".")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _extra_hosts() -> set[str]:
    return {
        item.strip().lower().rstrip(".")
        for item in (settings.local_allowed_hosts or "").split(",")
        if item.strip()
    }


def host_allowed(host_header: str) -> bool:
    """
    Host 是否可信。

    IP 字面量一律放行：DNS 重绑定必须借助域名，直接按 IP 访问不可能是重绑定；
    `*.localhost` 由浏览器直接解析到回环地址，同样不经过 DNS。
    """
    extra = _extra_hosts()
    if "*" in extra:
        return True
    host = _hostname(host_header)
    if not host:
        return False
    return (
        _is_ip_literal(host)
        or host == "localhost"
        or host.endswith(".localhost")
        or host in extra
    )


def origin_allowed(origin: str, host_header: str) -> bool:
    """写操作的 Origin 是否可信：与当前站点同源，或在 CORS 白名单内"""
    if not origin or origin == "null":
        return False
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.netloc.lower() == (host_header or "").strip().lower():
        return True
    allowed = {item.rstrip("/").lower() for item in settings.cors_origins_list if item}
    return origin.rstrip("/").lower() in allowed


class LocalAccessGuard:
    """纯 ASGI 中间件：不包装响应体，流式接口（SSE）不受影响"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or []
        }
        host = headers.get("host", "")

        if not host_allowed(host):
            await self._reject(
                scope, send,
                f"拒绝访问：不受信任的主机名 {_hostname(host) or '(空)'}。"
                "如需通过该域名访问，请在 LOCAL_ALLOWED_HOSTS 中显式添加。",
            )
            return

        method = str(scope.get("method", "GET")).upper()
        if scope["type"] == "websocket" or method not in _SAFE_METHODS:
            origin = headers.get("origin")
            if origin is not None:
                if not origin_allowed(origin, host):
                    await self._reject(scope, send, "拒绝访问：跨站请求不能操作本地服务。")
                    return
            elif headers.get("sec-fetch-site") == "cross-site":
                await self._reject(scope, send, "拒绝访问：跨站请求不能操作本地服务。")
                return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope, send, message: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps(
            {"code": "LOCAL_ACCESS_DENIED", "message": message, "details": None},
            ensure_ascii=False,
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})


def install_local_access_guard(app) -> None:
    """本地模式下安装访问守卫；其他模式为空操作（服务端部署由真实鉴权保护）"""
    if not settings.is_local_mode:
        return
    app.add_middleware(LocalAccessGuard)
