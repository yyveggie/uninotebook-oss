"""
认证相关路由

包含: 注册、登录、Token刷新、密码重置、邮箱验证码、用户信息
"""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Body, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import db_models
from config import settings, ENVIRONMENT
from database import get_db
from notification_service import send_verification_email_async
from notification_service import send_register_sms_async, send_reset_password_sms_async, SmsSendError
from cache_service import warmup_user_cache
from utils.logger import get_logger
from routes._errors import raise_http_error, error_detail
from routes.error_codes import ErrorCode
logger = get_logger(__name__)

router = APIRouter()


def _normalize_auth_cookie_domain(request_host: str, configured_domain: str | None) -> str | None:
    """
    计算 Set-Cookie 的 domain。

    域名一律来自 `AUTH_COOKIE_DOMAIN` 配置，不在代码里硬编码任何具体站点——
    否则换域名部署、或本地/私有部署时都会拿到一个无效的 domain，
    导致浏览器直接拒收 Cookie。未配置时返回 None（Cookie 仅限当前 host）。
    """
    host = (request_host or "").strip().lower()
    raw_domain = (configured_domain or "").strip().lower()

    # 未配置：不设置 domain，Cookie 作用域即当前 host（本地部署的正常情形）
    if not raw_domain:
        return None

    domain = raw_domain.lstrip(".")

    # 配置为主域时提升到 .example.com 形式，以兼容 www / apex / 子域间切换
    if host == domain or host.endswith(f".{domain}"):
        return f".{domain}"

    # 防御：配置域名与当前 host 不匹配时不设置 domain，避免 Set-Cookie 被浏览器拒绝
    return None


def _resolve_auth_cookie_config(request: Request) -> tuple[str | None, bool, str]:
    configured_domain = (settings.auth_cookie_domain or "").strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = forwarded_host or request.url.hostname or ""
    domain = _normalize_auth_cookie_domain(host, configured_domain)

    configured_secure = settings.auth_cookie_secure
    if configured_secure is None:
        forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        secure = forwarded_proto == "https" or request.url.scheme == "https" or bool(domain)
    else:
        secure = bool(configured_secure)

    samesite = (settings.auth_cookie_samesite or "lax").lower()
    if samesite not in {"lax", "strict", "none"}:
        samesite = "lax"

    return domain, secure, samesite


def set_refresh_token_cookie(request: Request, response: Response, refresh_token: str) -> None:
    """下发 HttpOnly 的 refresh_token Cookie（登录、刷新轮换、首屏恢复共用同一套参数）"""
    domain, secure, samesite = _resolve_auth_cookie_config(request)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
    )
PHONE_PLACEHOLDER_SUFFIX = "@phone.uninotebook.local"

# 发码接口对「账号是否存在」一律返回同一句话（防邮箱 / 手机号枚举），
# 措辞兼顾真实用户：收不到码时知道该去登录或找回密码
REGISTER_CODE_SENT_MESSAGE = "验证码已发送，请查收。若迟迟未收到，该账号可能已注册，请直接登录或找回密码"
RESET_CODE_SENT_MESSAGE = "若该手机号已注册，重置验证码将发送至该手机，请注意查收"


# ==================== 认证API ====================



# 允许的邮箱域名列表
ALLOWED_EMAIL_DOMAINS = {
    'gmail.com', 'qq.com', 'hotmail.com', 'outlook.com', 
    '163.com', '126.com', 'yahoo.com', 'live.com', 
    'icloud.com', 'sina.com', 'aliyun.com', 'foxmail.com',
}

# 允许的教育邮箱后缀
ALLOWED_EMAIL_SUFFIXES = {'.edu', '.edu.cn', '.ac.uk', '.ac.jp', '.edu.au'}

def is_valid_email_domain(email: str) -> bool:
    try:
        domain = email.split('@')[1].lower()
        if domain in ALLOWED_EMAIL_DOMAINS:
            return True
        return any(domain.endswith(suffix) for suffix in ALLOWED_EMAIL_SUFFIXES)
    except IndexError:
        return False


def get_register_mode() -> str:
    mode = (settings.auth_register_mode or "auto").strip().lower()
    if mode in {"email_only", "email_phone"}:
        return mode
    return "email_only" if ENVIRONMENT == "production" else "email_phone"


def is_phone_register_enabled() -> bool:
    return get_register_mode() == "email_phone"

@router.post("/api/auth/send-verification-code", tags=["认证"])
async def send_email_verification_code(
    payload: auth.EmailVerificationRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        # 1. 验证邮箱域名
        if not is_valid_email_domain(payload.email):
             raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="目前仅支持主流邮箱注册 (Gmail, QQ, Outlook, 163, 126等)")

        # 已注册的邮箱：不发验证码，但返回与正常发送完全相同的响应，
        # 否则任何人都能借此逐个探测某邮箱是否在本站注册过
        if await auth.get_user_by_email(db, payload.email):
            logger.info("ℹ️ 注册验证码请求命中已注册邮箱，未发送")
            return {"success": True, "message": REGISTER_CODE_SENT_MESSAGE}

        code, _ = await auth.prepare_email_verification_code(db, payload.email)

        try:
            await send_verification_email_async(
                payload.email,
                code,
                expiration_minutes=auth.VERIFICATION_CODE_TTL_MINUTES
            )
        except Exception as exc:
            await db.rollback()
            raise HTTPException(
                status_code=500,
                detail=error_detail(
                    code=ErrorCode.AUTH_500_SEND_CODE_FAILED,
                    message="验证码发送失败，请稍后再试",
                    details=str(exc),
                ),
            ) from exc

        await db.commit()

        return {"success": True, "message": REGISTER_CODE_SENT_MESSAGE}
    except HTTPException as e:
        logger.warning("⚠️ 发送验证码失败: email=%s status=%s reason=%s", payload.email, e.status_code, e.detail)
        raise e
    except Exception as e:
        await db.rollback()
        raise_http_error(status_code=500, code=ErrorCode.AUTH_500_EXCEPTION, message="发送验证码失败", details=str(e))


@router.post("/api/auth/send-password-reset-code", tags=["认证"])
async def send_password_reset_code(
    payload: auth.SmsVerificationRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        # 未注册的手机号：不发短信，但响应与正常发送一致，避免借重置接口探测手机号是否注册
        user = await auth.get_user_by_phone(db, payload.phone)
        if not user:
            logger.info("ℹ️ 重置验证码请求命中未注册手机号，未发送")
            return {"success": True, "message": RESET_CODE_SENT_MESSAGE}

        code, _ = await auth.prepare_sms_verification_code(db, payload.phone, purpose="reset_password")

        try:
            await send_reset_password_sms_async(
                payload.phone,
                code,
                expiration_minutes=auth.VERIFICATION_CODE_TTL_MINUTES
            )
        except SmsSendError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=500,
                detail=error_detail(
                    code=ErrorCode.AUTH_500_SEND_CODE_FAILED,
                    message="验证码发送失败，请稍后再试",
                    details=str(exc),
                ),
            ) from exc

        await db.commit()

        return {"success": True, "message": RESET_CODE_SENT_MESSAGE}
    except HTTPException as e:
        raise e
    except Exception as e:
        await db.rollback()
        raise_http_error(status_code=500, code=ErrorCode.AUTH_500_EXCEPTION, message="发送验证码失败", details=str(e))


@router.post("/api/auth/send-sms-code", tags=["认证"])
async def send_sms_verification_code(
    payload: auth.SmsVerificationRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        if not is_phone_register_enabled():
            raise_http_error(status_code=403, code=ErrorCode.AUTH_403_MESSAGE, message="当前环境仅支持邮箱注册")

        # 已注册的手机号：同样不发短信、返回相同响应（防手机号枚举）
        if await auth.get_user_by_phone(db, payload.phone):
            logger.info("ℹ️ 注册短信请求命中已注册手机号，未发送")
            return {"success": True, "message": REGISTER_CODE_SENT_MESSAGE}

        code, _ = await auth.prepare_sms_verification_code(db, payload.phone)

        try:
            await send_register_sms_async(
                payload.phone,
                code,
                expiration_minutes=auth.VERIFICATION_CODE_TTL_MINUTES
            )
        except SmsSendError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=500,
                detail=error_detail(
                    code=ErrorCode.AUTH_500_SEND_SMS_FAILED,
                    message="发送短信验证码失败",
                    details=str(exc),
                ),
            ) from exc

        await db.commit()
        return {"success": True, "message": REGISTER_CODE_SENT_MESSAGE}
    except HTTPException as e:
        raise e
    except Exception as e:
        await db.rollback()
        raise_http_error(status_code=500, code=ErrorCode.AUTH_500_EXCEPTION, message="发送短信验证码失败", details=str(e))


@router.post("/api/auth/reset-password", tags=["认证"])
async def reset_password(
    payload: auth.UserResetPassword,
    db: AsyncSession = Depends(get_db)
):
    try:
        # 1. 验证验证码
        await auth.verify_sms_code(db, payload.phone, payload.verification_code, purpose="reset_password")
        
        # 2. 获取用户
        user = await auth.get_user_by_phone(db, payload.phone)
        if not user:
            raise_http_error(status_code=404, code=ErrorCode.AUTH_404_MESSAGE, message="用户不存在")
            
        # 3. 更新密码
        user.hashed_password = auth.get_password_hash(payload.new_password)
        await db.commit()
        
        # 4. 吊销此前签发的全部 Token（含其他设备上的 refresh_token），强制重新登录
        await auth.revoke_user_tokens(user.id)
        
        logger.info(f"✅ 用户重置密码成功: {user.username}")
        return {"success": True, "message": "密码重置成功，请使用新密码登录"}
        
    except HTTPException as e:
        raise e
    except Exception as e:
        await db.rollback()
        raise_http_error(status_code=500, code=ErrorCode.AUTH_500_EXCEPTION, message="重置密码失败", details=str(e))


@router.post("/api/auth/register", response_model=auth.UserResponse, tags=["认证"])
async def register(user: auth.UserRegister, db: AsyncSession = Depends(get_db)):
    """
    用户注册
    
    创建新用户账号
    """
    try:
        # 注册时必须校验验证码（邮箱/短信二选一）
        code = (user.verification_code or "").strip()
        if not code:
            raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="请输入验证码")

        email = (str(user.email).strip().lower() if user.email else None)
        phone = user.phone

        if email:
            if not is_valid_email_domain(email):
                raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="目前仅支持主流邮箱注册")
            await auth.verify_email_code(db, email, code, auto_commit=False)
        elif phone:
            if not is_phone_register_enabled():
                raise_http_error(status_code=403, code=ErrorCode.AUTH_403_MESSAGE, message="当前环境仅支持邮箱注册")
            await auth.verify_sms_code(db, phone, code, auto_commit=False)
        else:
            raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="邮箱和手机号至少需要填写一个")

        # 邀请码验证（可选）
        invite_code_str = (user.invite_code or "").strip()
        invited_by_id = None
        
        if invite_code_str:
            # 1. 先查询是否是用户的专属邀请码
            user_result = await db.execute(
                select(db_models.User).filter(db_models.User.my_invite_code == invite_code_str)
            )
            inviter = user_result.scalars().first()
            
            if inviter:
                invited_by_id = inviter.id
            else:
                # 2. 如果不是用户邀请码，查询系统邀请码
                result = await db.execute(
                    select(db_models.InviteCode).filter(db_models.InviteCode.code == invite_code_str)
                )
                invite_record = result.scalars().first()
                
                if not invite_record:
                    raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="邀请码无效")
                    
                if not invite_record.is_active:
                    raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="邀请码已停用")
                    
                if invite_record.used_count >= invite_record.max_uses:
                    raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="邀请码已失效（达到最大使用次数）")
                    
                if invite_record.expires_at and invite_record.expires_at < datetime.now(timezone.utc):
                    raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="邀请码已过期")
                    
                # 原子地增加使用次数：条件 UPDATE 防止并发注册超发 (将在 create_user 的 commit 中一起提交)
                consume_result = await db.execute(
                    update(db_models.InviteCode)
                    .where(
                        db_models.InviteCode.id == invite_record.id,
                        db_models.InviteCode.used_count < db_models.InviteCode.max_uses,
                    )
                    .values(used_count=db_models.InviteCode.used_count + 1)
                    .execution_options(synchronize_session=False)
                )
                if consume_result.rowcount != 1:
                    raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="邀请码已失效（达到最大使用次数）")
        
        # is_vip 逻辑已移除

        user_payload = auth.UserCreate(
            email=email,
            phone=phone,
            username=user.username,
            password=user.password,
            full_name=user.full_name,
            invited_by_id=invited_by_id
        )
        db_user = await auth.create_user(db, user_payload)
        
        # 注册成功后，不直接返回 Token，让前端去调用 login 接口
        return db_user
    except HTTPException as e:
        await db.rollback()
        logger.warning(
            "⚠️ 注册失败: status=%s email=%s phone=%s reason=%s",
            e.status_code,
            (str(user.email).strip().lower() if user.email else None),
            user.phone,
            e.detail,
        )
        raise e
    except Exception as e:
        await db.rollback()
        logger.exception("❌ 注册异常: %s", e)
        raise_http_error(status_code=500, code=ErrorCode.AUTH_500_EXCEPTION, message="注册失败，请稍后再试")


@router.post("/api/auth/login", response_model=auth.Token, tags=["认证"])
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """
    用户登录
    
    使用用户名/邮箱和密码登录，返回Access Token和Refresh Token，并设置HttpOnly Cookie
    """
    # 验证用户
    user = await auth.authenticate_user(db, form_data.username, form_data.password)
    
    if not user:
        raise HTTPException(
            status_code=401,
            detail="账号或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 创建Token
    access_token = auth.create_access_token(
        data={"sub": user.username, "user_id": user.id}
    )
    refresh_token = auth.create_refresh_token(
        data={"sub": user.username, "user_id": user.id}
    )
    
    # 设置 HttpOnly Cookie
    # 注意：跨子域部署时需通过 AUTH_COOKIE_DOMAIN 配置主域（形如 ".example.com"）
    # secure=True 要求 HTTPS
    domain, secure, samesite = _resolve_auth_cookie_config(request)
    
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60
    )
    
    # 后台预热缓存（使用独立 session，避免请求级 session 关闭后报错）
    from database import get_async_session_maker
    async def _warmup():
        try:
            async with get_async_session_maker()() as bg_db:
                await warmup_user_cache(user.id, bg_db)
        except Exception as e:
            logger.warning(f"⚠️ 缓存预热失败: {e}")
    asyncio.create_task(_warmup())
    
    logger.info(f"✅ 用户登录成功: {user.username}")
    
    # refresh_token 只通过 HttpOnly Cookie 下发，不放进响应体（避免被前端脚本读取）
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }


@router.post("/api/auth/logout", tags=["认证"])
async def logout(request: Request, response: Response):
    """
    用户登出
    
    清除Cookie中的Token，并将当前的Refresh Token加入黑名单
    """
    domain, _, _ = _resolve_auth_cookie_config(request)
    
    # 尝试获取当前的 refresh_token 并加入黑名单
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        try:
            token_data = auth.decode_token(refresh_token, token_type="refresh")
            # 计算剩余过期时间
            expires_in = settings.refresh_token_expire_days * 24 * 60 * 60
            await auth.blacklist_token(refresh_token, expires_in)
        except Exception:
            pass # 忽略无效的 token
            
    response.delete_cookie(key="refresh_token", domain=domain, path="/")
    
    return {"message": "登出成功"}


@router.post("/api/auth/refresh", response_model=auth.Token, tags=["认证"])
async def refresh_token(
    request: Request,
    response: Response,
    data: dict = Body(None), 
    db: AsyncSession = Depends(get_db)
):
    """
    刷新Token
    
    优先从Cookie获取Refresh Token，获取新的Access Token并更新Cookie
    """
    try:
        # 1. 优先从 Cookie 获取 refresh_token
        refresh_token = request.cookies.get("refresh_token")
        
        # 2. 如果 Cookie 没有，尝试从请求体获取 (兼容旧版)
        if not refresh_token and data:
            refresh_token = data.get("refresh_token")
            
        if not refresh_token:
            raise_http_error(status_code=401, code=ErrorCode.AUTH_401_MESSAGE, message="缺少 refresh_token，请重新登录")
            
        # 宽限期：同一个旧 Token 的并发 / 迟到请求复用同一次轮换结果（见 auth.rotate_refresh_token）
        rotated = await auth.get_rotated_refresh_pair(refresh_token)
        if rotated is None:
            # 检查是否已失效（登出拉黑 / 已轮换 / 重置密码后的用户级吊销）
            if await auth.is_token_blacklisted(refresh_token):
                # 已轮换的旧 Token 在宽限期后再次出现 → 疑似泄露，吊销该用户全部会话
                await auth.handle_refresh_token_reuse(refresh_token)
                raise_http_error(status_code=401, code=ErrorCode.AUTH_401_MESSAGE, message="Refresh Token 已失效，请重新登录")

            token_data = auth.decode_token(refresh_token, token_type="refresh")
            user = await auth.get_user_by_id(db, token_data.user_id)
            if not user or not user.is_active:
                raise HTTPException(
                    status_code=401,
                    detail="用户不存在"
                )

            # 单飞：同一 Token 的并发刷新只轮换一次，其余请求等待并复用结果
            if await auth.acquire_refresh_singleflight(refresh_token):
                try:
                    rotated = await auth.rotate_refresh_token(refresh_token, user)
                finally:
                    await auth.release_refresh_singleflight(refresh_token)
            else:
                rotated = await auth.wait_for_rotated_refresh_pair(refresh_token)
                if rotated is None:
                    # 持锁请求迟迟没有结果：只签 access_token，Cookie 由持锁请求的响应更新
                    rotated = (
                        auth.create_access_token(data={"sub": user.username, "user_id": user.id}),
                        None,
                    )

        new_access_token, new_refresh_token = rotated
        if new_refresh_token:
            set_refresh_token_cookie(request, response, new_refresh_token)

        # refresh_token 只通过 HttpOnly Cookie 下发，不放进响应体
        return {
            "access_token": new_access_token,
            "token_type": "bearer"
        }
        
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail="Token无效或已过期"
        )


@router.get("/api/auth/me", response_model=auth.UserResponse, tags=["认证"])
async def get_current_user_info(current_user: db_models.User = Depends(auth.get_current_user)):
    """
    获取当前用户信息
    
    需要在请求头中携带有效的Access Token
    """
    return current_user


@router.get("/api/users/{user_id}", response_model=auth.UserResponse, tags=["用户"])
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    获取用户信息
    
    需要认证
    """
    user = await auth.get_user_by_id(db, user_id)
    
    if not user:
        raise_http_error(status_code=404, code=ErrorCode.AUTH_404_MESSAGE, message="用户不存在")
    
    return user


@router.get("/api/auth/account-bindings", response_model=auth.AccountBindingsResponse, tags=["认证"])
async def get_account_bindings(current_user: db_models.User = Depends(auth.get_current_user)):
    """获取当前账号的邮箱/手机号绑定状态。"""
    email = current_user.email
    has_real_email = bool(email) and not str(email).endswith(PHONE_PLACEHOLDER_SUFFIX)
    return {
        "email": email if has_real_email else None,
        "phone": current_user.phone,
        "has_email": has_real_email,
        "has_phone": bool(current_user.phone),
    }


@router.post("/api/auth/send-bind-email-code", tags=["认证"])
async def send_bind_email_code(
    payload: auth.EmailVerificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """发送绑定邮箱验证码。"""
    try:
        target_email = payload.email.strip().lower()

        if not is_valid_email_domain(target_email):
            raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="目前仅支持主流邮箱")

        existing = await auth.get_user_by_email(db, target_email)
        if existing and existing.id != current_user.id:
            raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="该邮箱已被其他账号使用")

        code, _ = await auth.prepare_email_verification_code(db, target_email, purpose="bind_email")

        try:
            await send_verification_email_async(
                target_email,
                code,
                expiration_minutes=auth.VERIFICATION_CODE_TTL_MINUTES
            )
        except Exception as exc:
            await db.rollback()
            raise HTTPException(
                status_code=500,
                detail=error_detail(
                    code=ErrorCode.AUTH_500_SEND_CODE_FAILED,
                    message="验证码发送失败，请稍后再试",
                    details=str(exc),
                ),
            ) from exc

        await db.commit()
        return {"success": True, "message": "绑定邮箱验证码已发送"}
    except HTTPException as e:
        raise e
    except Exception as e:
        await db.rollback()
        raise_http_error(status_code=500, code=ErrorCode.AUTH_500_EXCEPTION, message="发送验证码失败", details=str(e))


@router.post("/api/auth/send-bind-phone-code", tags=["认证"])
async def send_bind_phone_code(
    payload: auth.SmsVerificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """发送绑定手机号验证码。"""
    try:
        existing = await auth.get_user_by_phone(db, payload.phone)
        if existing and existing.id != current_user.id:
            raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="该手机号已被其他账号使用")

        code, _ = await auth.prepare_sms_verification_code(db, payload.phone, purpose="bind_phone")

        try:
            await send_register_sms_async(
                payload.phone,
                code,
                expiration_minutes=auth.VERIFICATION_CODE_TTL_MINUTES
            )
        except SmsSendError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=500,
                detail=error_detail(
                    code=ErrorCode.AUTH_500_SEND_SMS_FAILED,
                    message="发送短信验证码失败",
                    details=str(exc),
                ),
            ) from exc

        await db.commit()
        return {"success": True, "message": "绑定手机号验证码已发送"}
    except HTTPException as e:
        raise e
    except Exception as e:
        await db.rollback()
        raise_http_error(status_code=500, code=ErrorCode.AUTH_500_EXCEPTION, message="发送验证码失败", details=str(e))


@router.post("/api/auth/bind-email", response_model=auth.UserResponse, tags=["认证"])
async def bind_email(
    payload: auth.BindEmailRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """绑定或更新当前账号邮箱。"""
    try:
        target_email = payload.email.strip().lower()
        code = payload.verification_code.strip()
        if not code:
            raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="请输入验证码")

        if not is_valid_email_domain(target_email):
            raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="目前仅支持主流邮箱")

        existing = await auth.get_user_by_email(db, target_email)
        if existing and existing.id != current_user.id:
            raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="该邮箱已被其他账号使用")

        await auth.verify_email_code(db, target_email, code, purpose="bind_email")

        current_user.email = target_email
        db.add(current_user)
        await db.commit()
        await db.refresh(current_user)

        return current_user
    except HTTPException as e:
        raise e
    except Exception as e:
        await db.rollback()
        raise_http_error(status_code=500, code=ErrorCode.AUTH_500_EXCEPTION, message="绑定邮箱失败", details=str(e))


@router.post("/api/auth/bind-phone", response_model=auth.UserResponse, tags=["认证"])
async def bind_phone(
    payload: auth.BindPhoneRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """绑定或更新当前账号手机号。"""
    try:
        code = payload.verification_code.strip()
        if not code:
            raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="请输入验证码")

        existing = await auth.get_user_by_phone(db, payload.phone)
        if existing and existing.id != current_user.id:
            raise_http_error(status_code=400, code=ErrorCode.AUTH_400_MESSAGE, message="该手机号已被其他账号使用")

        await auth.verify_sms_code(db, payload.phone, code, purpose="bind_phone")

        current_user.phone = payload.phone
        db.add(current_user)
        await db.commit()
        await db.refresh(current_user)

        return current_user
    except HTTPException as e:
        raise e
    except Exception as e:
        await db.rollback()
        raise_http_error(status_code=500, code=ErrorCode.AUTH_500_EXCEPTION, message="绑定手机号失败", details=str(e))
