"""
FastAPI主应用

应用入口：负责生命周期管理、中间件配置、路由注册。
业务路由已拆分至 routes/ 目录下的各模块。
"""
import time
import hashlib
import asyncio
import logging
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import uvicorn
from contextlib import asynccontextmanager

from utils.logger import configure_logging, get_logger, log_error, log_info
from config import settings
from database import get_db, init_db
import db_models

# 抑制 pdfminer 的非致命警告
configure_logging()
logger = get_logger(__name__)
logging.getLogger("pdfminer.pdfinterp").setLevel(logging.ERROR)


# ==================== 生命周期 ====================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    
    # 启动时初始化
    logger.info("🚀 正在启动 UniNotebook...")

    # 服务端部署：生产环境拒绝以默认/过短的 JWT 密钥启动（否则任何人都能伪造登录态）
    if settings.is_saas_mode:
        from auth import validate_security_settings
        validate_security_settings()

    # 初始化数据库（异步）
    if settings.db_auto_init:
        try:
            await init_db()
            logger.info("✅ 数据库初始化成功（异步模式）")
        except Exception as e:
            if settings.is_local_mode:
                raise
            logger.warning("⚠️ 数据库初始化失败: %s", str(e))
            logger.warning("⚠️ 建议使用迁移脚本或为数据库用户授予 schema/table 创建权限")
    else:
        logger.info("ℹ️ 已跳过自动建表（DB_AUTO_INIT=false）")

    # 本地模式：确保固定单用户存在，并把任务派发切到进程内执行
    if settings.is_local_mode:
        try:
            from runtime.identity import ensure_local_user
            await ensure_local_user()
        except Exception as e:
            logger.exception("⚠️ 本地单用户初始化失败: %s", str(e))
            raise

        # 新数据目录：写入示例文档，打开就有内容可以体验（失败不影响启动）
        try:
            from runtime.identity import LOCAL_USER_ID
            from runtime.samples import seed_local_samples_once
            await seed_local_samples_once(LOCAL_USER_ID)
        except Exception as e:
            logger.warning("⚠️ 示例文档写入失败: %s", e)

        try:
            from runtime.tasks import install_task_overrides, initialize_local_tasks
            install_task_overrides()
            await asyncio.to_thread(initialize_local_tasks)
        except Exception as e:
            logger.exception("⚠️ 本地任务执行器初始化失败: %s", str(e))
            raise

        # 后台清理上次运行中途退出遗留的孤立向量集合（不阻塞启动；只删 6 小时前创建且无人引用的）
        async def _sweep_orphan_vectors():
            try:
                from database import AsyncSessionLocal
                from document_service.vector_maintenance import sweep_orphan_collections
                await sweep_orphan_collections(AsyncSessionLocal)
            except Exception as e:
                logger.warning("⚠️ 孤立向量集合清理失败: %s", e)

        app.state.orphan_vector_sweep = asyncio.create_task(_sweep_orphan_vectors())

    # 初始化面试管理器。
    # 它在使用时才按当前凭据解析模型（见 InterviewManager.lc_client），创建时不需要 Key。
    # 本地版过去在没有 OpenAI 兼容端点的 Key 时跳过创建，结果启动后才在设置页填 Key、
    # 或只配置了通义千问 / Kimi / DeepSeek 时，AI 面试一直报「面试管理器未初始化」。
    from runtime.credentials import credential_store

    try:
        from document_service import initialize_interview_manager
        initialize_interview_manager(
            api_key=credential_store.api_key("openai"),
            base_url=credential_store.base_url("openai") or "https://api.openai.com/v1",
            model=settings.openai_model
        )
        logger.info("✅ 面试管理器已初始化")
    except Exception as e:
        if settings.is_local_mode:
            logger.warning("⚠️ 面试管理器初始化失败（可在设置页检查 API Key）: %s", str(e))
        else:
            logger.exception("⚠️ 面试管理器初始化失败: %s", str(e))
    
    yield
    
    # 关闭时清理
    logger.info("👋 应用关闭")

    # 关闭Redis连接
    try:
        from cache_service import close_redis
        await close_redis()
    except Exception as e:
        logger.warning("⚠️ 关闭Redis失败: %s", e)

    # 本地模式：关闭进程内任务线程池
    if settings.is_local_mode:
        try:
            from runtime.tasks import shutdown_local_executor
            shutdown_local_executor()
        except Exception as e:
            logger.warning("⚠️ 关闭本地任务线程池失败: %s", e)


# ==================== 创建应用 ====================


app = FastAPI(
    title="UniNotebook API",
    description="AI驱动的个性化学习路径生成系统",
    version="1.0.0",
    lifespan=lifespan
)


# ==================== 中间件配置 ====================


# 限流中间件（必须在CORS之前）
from utils.rate_limiter import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)
logger.info("🛡️ 请求限流中间件已启用")

# CORS中间件
cors_origins = settings.cors_origins_list if hasattr(settings, 'cors_origins_list') else ["http://localhost:3000", "http://localhost:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 超大请求在读取请求体之前直接 413（multipart 会先整体落盘，路由内校验来不及）
from utils.request_limits import RequestSizeLimitMiddleware
app.add_middleware(RequestSizeLimitMiddleware)

# 本地模式：免登录的本地服务必须挡住 DNS 重绑定与跨站写请求（其他模式为空操作）。
# 最后添加 = 最外层，先于限流与 CORS 执行。
from runtime.guard import install_local_access_guard
install_local_access_guard(app)


def _request_id_from_request(request: Request) -> str:
    state_request_id = getattr(request.state, "request_id", None)
    if state_request_id:
        return str(state_request_id)

    header_request_id = request.headers.get("X-Request-ID") or request.headers.get("X-Correlation-ID")
    if header_request_id:
        return header_request_id

    return ""


def _normalize_http_exception_detail(status_code: int, detail):
    if isinstance(detail, dict):
        code = detail.get("code") or f"HTTP_{status_code}"
        message = detail.get("message") or "请求失败"
        details = detail.get("details")
        return code, message, details

    if isinstance(detail, str):
        return f"HTTP_{status_code}", detail, None

    if detail is None:
        return f"HTTP_{status_code}", "请求失败", None

    return f"HTTP_{status_code}", "请求失败", detail


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or request.headers.get("X-Correlation-ID") or str(uuid4())
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    code, message, details = _normalize_http_exception_detail(exc.status_code, exc.detail)
    payload = {
        "code": code,
        "message": message,
        "details": details,
        "request_id": _request_id_from_request(request),
    }
    return JSONResponse(status_code=exc.status_code, content=payload, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    payload = {
        "code": "VALIDATION_ERROR",
        "message": "请求参数校验失败",
        # 自定义校验器抛出的 ValueError 会原样放在 ctx 里，必须先转成可序列化的结构，否则 422 变成 500
        "details": jsonable_encoder(exc.errors()),
        "request_id": _request_id_from_request(request),
    }
    return JSONResponse(status_code=422, content=payload)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from runtime.features import looks_like_missing_credentials, missing_credential_hint

    # 缺 API Key 不是「服务器内部错误」，而是一个用户可自行解决的配置问题。
    # 各家 SDK 抛出的原始文本（"The api_key client option must be set…"）
    # 对用户毫无指引，这里统一翻译成「缺哪个、去哪填」。
    # 放在全局处理器里，是因为这类错误会打到每一个生成类接口，逐个打补丁必然漏。
    if looks_like_missing_credentials(exc):
        logger.warning("⚠️ 凭据未配置或无效: %s", str(exc))
        payload = {
            "code": "CREDENTIALS_NOT_CONFIGURED",
            "message": missing_credential_hint(),
            "details": None,
            "request_id": _request_id_from_request(request),
        }
        # 503：服务暂不可用，补齐配置后即可恢复
        return JSONResponse(status_code=503, content=payload)

    logger.exception("❌ 未处理异常: %s", str(exc))
    payload = {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "服务器内部错误",
        "details": None,
        "request_id": _request_id_from_request(request),
    }
    return JSONResponse(status_code=500, content=payload)


# 请求日志 + 性能监控中间件（合并）
@app.middleware("http")
async def log_requests(request, call_next):
    """记录所有HTTP请求 + 慢请求告警"""
    start_time = time.time()
    
    log_info(f"\n🔵 收到请求: {request.method} {request.url.path}")
    if request.query_params:
        log_info(f"   查询参数: {dict(request.query_params)}")
    
    try:
        response = await call_next(request)
        
        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = f"{process_time:.3f}"
        status_emoji = "✅" if response.status_code < 400 else "❌"
        log_info(f"{status_emoji} 响应: {response.status_code} | 耗时: {process_time:.2f}s\n")
        
        if process_time > 3.0:
            log_info(f"⚠️ 慢请求: {request.method} {request.url.path} - {process_time:.2f}s")
        
        return response
        
    except Exception as e:
        process_time = time.time() - start_time
        log_error(f"\n❌❌❌ 请求处理异常 ❌❌❌")
        log_error(f"路径: {request.method} {request.url.path}")
        log_error(f"错误: {str(e)}")
        log_error(f"耗时: {process_time:.2f}s\n")
        raise


# 访问统计写库任务的强引用：事件循环只弱引用 Task，不持有会被中途回收
_visit_log_tasks: set = set()


# 访问统计中间件
@app.middleware("http")
async def track_visits(request, call_next):
    """记录访问统计"""
    from database import get_async_session_maker

    excluded_paths = ['/health', '/docs', '/redoc', '/openapi.json', '/favicon.ico']
    # 本地版没有访问统计页面，逐请求写 SQLite 只会和后台任务争写锁
    should_track = settings.is_saas_mode and not any(
        request.url.path.startswith(path) for path in excluded_paths
    )
    
    if should_track:
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "")
        visitor_id = hashlib.md5(f"{client_ip}{user_agent}".encode()).hexdigest()
        
        async def log_visit():
            try:
                session_maker = get_async_session_maker()
                async with session_maker() as db:
                    user_id = None
                    auth_header = request.headers.get("authorization", "")
                    if auth_header.startswith("Bearer "):
                        try:
                            token = auth_header.replace("Bearer ", "")
                            from jose import jwt
                            payload = jwt.decode(
                                token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
                            )
                            user_id = payload.get("user_id")
                        except Exception:
                            pass
                    
                    visit_log = db_models.VisitLog(
                        visitor_id=visitor_id,
                        user_id=user_id,
                        ip_address=client_ip,
                        user_agent=user_agent[:500] if user_agent else None,
                        path=request.url.path[:500],
                        method=request.method
                    )
                    db.add(visit_log)
                    await db.commit()
            except Exception as e:
                log_error(f"⚠️ 记录访问失败: {e}")
        
        task = asyncio.create_task(log_visit())
        _visit_log_tasks.add(task)
        task.add_done_callback(_visit_log_tasks.discard)

    response = await call_next(request)
    return response


# ==================== 注册路由 ====================


# 已有的独立路由模块（知识图谱、PPT、海报）
from knowledge_graph import api as kg_api
from document_service.generators.ppt import api as ppt_api
from document_service.generators.poster import api as poster_api
from document_service.generators.podcast import api as podcast_api

app.include_router(kg_api.router)
app.include_router(ppt_api.router)
app.include_router(poster_api.router)
app.include_router(podcast_api.router)

# 从 routes/ 注册所有业务路由
from routes import register_routers
register_routers(app)

# 本地模式：把鉴权依赖整体替换为固定单用户（其他模式下为空操作）
from runtime.identity import install_identity_overrides
install_identity_overrides(app)

# 挂载静态文件目录用于问题报告图片
# 本地部署不提供问题报告功能（本地报告只会写进本机数据库，无人查看），因此不挂载，
# 也就不会凭空创建一个永远为空的 data/issues 目录。
from fastapi.staticfiles import StaticFiles

if settings.is_saas_mode:
    from runtime.paths import ensure_data_path
    _issues_dir = ensure_data_path("issues")
    app.mount("/static/issues", StaticFiles(directory=str(_issues_dir)), name="issues_static")

# 本地部署：由后端直接提供构建好的前端，用户只需 `python run.py` 一条命令。
# 多用户服务端部署通常由 Nginx 提供静态资源，不走这里。
if settings.is_local_mode:
    from pathlib import Path as _Path

    _frontend_dist = _Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if (_frontend_dist / "index.html").exists():
        from fastapi.responses import FileResponse

        _index_html = _frontend_dist / "index.html"

        # 构建产物（带内容哈希，可长期缓存）
        app.mount(
            "/assets",
            StaticFiles(directory=str(_frontend_dist / "assets")),
            name="frontend_assets",
        )

        # SPA 回退：前端用的是 history 路由，/settings、/knowledgegraph 这类地址
        # 在服务端并不存在。直接刷新或深链接访问时必须回落到 index.html，
        # 由前端路由接管，否则会返回 404。
        #
        # 用 **404 处理器** 而非 catch-all 路由来实现，原因是：
        # - `StaticFiles(html=True)` 只对**目录**请求回退，任意子路径仍 404
        #   （本地版曾因此在任何非首页路径刷新即报错）；
        # - `@app.get("/{path:path}")` catch-all 会让 POST 到不存在的 API 路径
        #   返回 405「方法不允许」而非 404——因为路径确实匹配上了一个 GET 路由。
        # 走 404 处理器则不引入任何新路由，各方法的语义都保持正确。
        _dist_root = _frontend_dist.resolve()

        @app.exception_handler(404)
        async def spa_fallback(request: Request, exc):  # noqa: ANN001
            path = request.url.path.lstrip("/")

            # API 与后端静态资源保持 JSON 404，绝不返回 HTML
            is_api = path.startswith(("api/", "static/", "docs", "openapi.json", "redoc"))
            if request.method != "GET" or is_api:
                code, message, details = _normalize_http_exception_detail(404, getattr(exc, "detail", None))
                return JSONResponse(
                    status_code=404,
                    content={
                        "code": code,
                        "message": message,
                        "details": details,
                        "request_id": _request_id_from_request(request),
                    },
                )

            # 真实存在的静态文件（favicon、图片等）直接返回
            if path:
                candidate = (_frontend_dist / path).resolve()
                if _dist_root in candidate.parents and candidate.is_file():
                    return FileResponse(candidate)

            # 其余一律交给前端路由
            return FileResponse(_index_html)

        logger.info("🖥️ 已托管前端静态资源: %s", _frontend_dist)
    else:
        logger.warning(
            "⚠️ 未找到前端构建产物（%s）。请先执行：cd frontend && npm install && npm run build",
            _frontend_dist,
        )


# ==================== 根路径 ====================


@app.get("/")
async def root():
    """
    根路径

    本地部署由本进程同时提供前端，根路径必须返回应用首页——
    否则用户打开 http://127.0.0.1:8000 看到的是一段 JSON。
    服务端部署下前端由 Nginx 提供，这里返回 API 信息即可。
    """
    if settings.is_local_mode:
        from pathlib import Path as _P

        from fastapi.responses import FileResponse as _FileResponse

        index = _P(__file__).resolve().parent.parent / "frontend" / "dist" / "index.html"
        if index.exists():
            return _FileResponse(index)

    return {
        "message": "欢迎使用 UniNotebook API",
        "version": "1.0.0",
        "docs": "/docs"
    }


# ==================== 启动入口 ====================


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level="info"
    )
