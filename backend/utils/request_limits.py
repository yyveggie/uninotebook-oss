"""
请求体大小上限

Starlette 解析 multipart 时会先把整个请求体落盘，路由里的大小校验只能限制内存占用，
挡不住超大请求本身。本中间件在读取请求体**之前**按 Content-Length 直接拒绝。

服务端部署前面还有 Nginx 的 client_max_body_size；本地版直接由 uvicorn 对外，
只能靠这一层。
"""
from __future__ import annotations

import json

from config import settings

# multipart 边界与普通表单字段的额外开销
_MULTIPART_OVERHEAD_BYTES = 1024 * 1024


class RequestSizeLimitMiddleware:
    """纯 ASGI 中间件：不包装响应体，流式接口（SSE）不受影响"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            limit = settings.max_upload_mb * 1024 * 1024 + _MULTIPART_OVERHEAD_BYTES
            for key, value in scope.get("headers") or []:
                if key != b"content-length":
                    continue
                try:
                    size = int(value)
                except ValueError:
                    size = 0
                if size > limit:
                    await self._reject(send)
                    return
                break
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send) -> None:
        body = json.dumps(
            {
                "code": "PAYLOAD_TOO_LARGE",
                "message": f"上传内容过大，单个文件不能超过 {settings.max_upload_mb} MB",
                "details": None,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
