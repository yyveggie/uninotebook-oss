"""
认证模块 - JWT + bcrypt
"""
from datetime import datetime, timedelta, timezone
import asyncio
import hashlib
import json
import time
from typing import Optional
import re
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request, Response
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
import secrets
import string
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from database import get_db
import db_models
from config import settings
from cache_service.redis_client import cache_set_bytes, cache_get_bytes, cache_delete, cache_set_nx_bytes, get_redis_client
from utils.logger import get_logger
logger = get_logger(__name__)

# 密码加密上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2密码承载令牌 (保留用于Swagger UI，但实际业务使用Cookie)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)

# ==================== Pydantic模型 ====================

class Token(BaseModel):
    """Token响应模型（refresh_token 仅通过 HttpOnly Cookie 下发，不出现在响应体中）"""
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Token数据模型"""
    username: Optional[str] = None
    user_id: Optional[int] = None
    issued_at: Optional[float] = None  # iat（秒），用于判断是否早于用户级会话吊销时间点


class UserCreate(BaseModel):
    """用户注册模型"""
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    username: str
    password: str
    full_name: Optional[str] = None
    invited_by_id: Optional[int] = None

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> Optional[str]:
        return normalize_cn_phone(value, allow_empty=True)

    @model_validator(mode='after')
    def validate_contact(self):
        if not self.email and not self.phone:
            raise ValueError("邮箱和手机号至少需要填写一个")
        return self


class UserRegister(BaseModel):
    """带验证码和邀请码的注册模型（支持邮箱/手机号）"""
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    username: str
    password: str
    full_name: Optional[str] = None
    verification_code: Optional[str] = None
    invite_code: Optional[str] = None  # 邀请码可选

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> Optional[str]:
        return normalize_cn_phone(value, allow_empty=True)

    @model_validator(mode='after')
    def validate_contact(self):
        if not self.email and not self.phone:
            raise ValueError("邮箱和手机号至少需要填写一个")
        return self


class EmailVerificationRequest(BaseModel):
    """发送验证码请求"""
    email: EmailStr


class SmsVerificationRequest(BaseModel):
    """发送短信验证码请求"""
    phone: str

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_cn_phone(value)


class UserResponse(BaseModel):
    """用户响应模型"""
    id: int
    email: str
    phone: Optional[str]
    username: str
    full_name: Optional[str]
    is_active: bool
    my_invite_code: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class UserResetPassword(BaseModel):
    """重置密码模型"""
    phone: str
    new_password: str
    verification_code: str

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_cn_phone(value)


class UserLogin(BaseModel):
    """用户登录模型"""
    username: str  # 可以是用户名或邮箱
    password: str


class BindEmailRequest(BaseModel):
    """绑定邮箱请求"""
    email: EmailStr
    verification_code: str


class BindPhoneRequest(BaseModel):
    """绑定手机号请求"""
    phone: str
    verification_code: str

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_cn_phone(value)


class AccountBindingsResponse(BaseModel):
    """当前账号绑定信息"""
    email: Optional[str]
    phone: Optional[str]
    has_email: bool
    has_phone: bool


VERIFICATION_CODE_TTL_MINUTES = 10
VERIFICATION_CODE_RESEND_SECONDS = 60
VERIFICATION_CODE_MAX_FAILURES = 5  # 同一目标+用途连续输错次数上限，达到后作废未使用的验证码
PHONE_REGEX = re.compile(r'^1[3-9]\d{9}$')


def normalize_cn_phone(phone: Optional[str], allow_empty: bool = False) -> Optional[str]:
    """归一化中国大陆手机号为11位数字。"""
    if phone is None:
        if allow_empty:
            return None
        raise ValueError("手机号不能为空")

    raw = phone.strip().replace(' ', '').replace('-', '')
    if not raw:
        if allow_empty:
            return None
        raise ValueError("手机号不能为空")

    if raw.startswith('+86'):
        raw = raw[3:]
    elif raw.startswith('86') and len(raw) == 13:
        raw = raw[2:]

    if not PHONE_REGEX.match(raw):
        raise ValueError("请输入有效的中国大陆手机号")

    return raw


# ==================== 密码处理 ====================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """密码哈希"""
    return pwd_context.hash(password)


# ==================== Token管理 ====================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    创建Access Token
    
    Args:
        data: Token中要包含的数据
        expires_delta: 过期时间增量
        
    Returns:
        JWT Token字符串
    """
    to_encode = data.copy()
    
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    
    to_encode.update({
        "exp": expire,
        "iat": now,
        "type": "access"
    })
    
    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm
    )
    
    return encoded_jwt


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    创建Refresh Token
    
    Args:
        data: Token中要包含的数据
        expires_delta: 过期时间增量
        
    Returns:
        JWT Token字符串
    """
    to_encode = data.copy()
    
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=settings.refresh_token_expire_days)
    
    to_encode.update({
        "exp": expire,
        "iat": now,
        "type": "refresh",
        # JWT 时间戳精确到秒：同一秒内为同一用户签发的 Token 原本逐字节相同，
        # 轮换时作废旧 Token 会连带作废「新」Token。唯一 ID 保证每次签发都不同。
        "jti": secrets.token_urlsafe(16),
    })

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_refresh_secret_key,
        algorithm=settings.jwt_algorithm
    )
    
    return encoded_jwt


async def blacklist_token(token: str, expires_in: int) -> None:
    """
    将Token加入黑名单（Redis）
    """
    try:
        await cache_set_bytes(f"blacklist:{token}", b"1", ttl=expires_in)
    except Exception as e:
        logger.warning(f"⚠️ 无法将Token加入黑名单: {e}")

async def is_token_blacklisted(token: str) -> bool:
    """
    检查Token是否已失效：
    - 该 Token 被单独拉黑（登出）
    - 签发时间不晚于该用户的会话吊销时间点（重置密码，见 revoke_user_tokens）
    """
    try:
        val = await cache_get_bytes(f"blacklist:{token}")
        if val is not None:
            return True
    except Exception as e:
        logger.warning(f"⚠️ 无法检查Token黑名单: {e}")
        return False

    # 用户级吊销：这里只读取未验签的 claims 来定位用户和签发时间，调用方随后仍会 decode_token 验签；
    # 伪造 claims 最多让 Token 被判为「已失效」，无法借此绕过校验。
    try:
        claims = jwt.get_unverified_claims(token)
    except Exception:
        return False
    return await is_user_token_revoked(claims.get("user_id"), claims.get("iat"))


def _tokens_valid_after_key(user_id) -> str:
    return f"auth:tokens_valid_after:{user_id}"


async def revoke_user_tokens(user_id: int) -> None:
    """
    使该用户此前签发的全部 Token（access + refresh）失效，用于重置密码等场景。

    在 Redis 记录吊销时间点（秒），校验时与 Token 的 iat 比较；保留时长与 refresh_token
    有效期一致——超过后旧 Token 本身也已过期。Redis 不可用时降级放行（与黑名单策略一致）。
    """
    revoked_at = int(datetime.now(timezone.utc).timestamp())
    try:
        await cache_set_bytes(
            _tokens_valid_after_key(user_id),
            str(revoked_at).encode("utf-8"),
            ttl=settings.refresh_token_expire_days * 24 * 60 * 60,
        )
    except Exception as e:
        logger.warning(f"⚠️ 无法吊销用户会话: {e}")


async def is_user_token_revoked(user_id, issued_at) -> bool:
    """
    Token 是否已被用户级吊销（签发时间 iat 不晚于吊销时间点）。

    iat 精度为秒，同一秒内签发的 Token 一并视为失效（宁可多要求一次登录）。
    缺少 user_id / iat、或 Redis 不可用时放行。
    """
    if user_id is None or issued_at is None:
        return False
    try:
        raw = await cache_get_bytes(_tokens_valid_after_key(user_id))
        if not raw:
            return False
        return float(issued_at) <= float(raw.decode("utf-8"))
    except Exception as e:
        logger.warning(f"⚠️ 无法检查用户会话吊销状态: {e}")
        return False


def _refresh_token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _refresh_lock_key(token: str) -> str:
    return f"refresh:lock:{_refresh_token_fingerprint(token)}"


def _refresh_result_key(token: str) -> str:
    return f"refresh:result:{_refresh_token_fingerprint(token)}"


async def get_cached_refresh_access_token(refresh_token: str) -> Optional[str]:
    """获取同一 refresh_token 在短窗口内已生成的 access_token（幂等）。"""
    try:
        cached = await cache_get_bytes(_refresh_result_key(refresh_token))
        if not cached:
            return None
        return cached.decode("utf-8")
    except Exception:
        return None


async def cache_refresh_access_token(refresh_token: str, access_token: str, ttl_seconds: int = 20) -> None:
    """缓存 refresh 结果，供并发请求复用。"""
    try:
        await cache_set_bytes(
            _refresh_result_key(refresh_token),
            access_token.encode("utf-8"),
            ttl=max(1, ttl_seconds),
        )
    except Exception:
        pass


async def acquire_refresh_singleflight(refresh_token: str, lock_ttl_seconds: int = 6) -> bool:
    """尝试获取 refresh 分布式锁（SET NX）。"""
    try:
        return await cache_set_nx_bytes(
            _refresh_lock_key(refresh_token),
            b"1",
            ttl=max(1, lock_ttl_seconds),
        )
    except Exception:
        return False


async def release_refresh_singleflight(refresh_token: str) -> None:
    """释放 refresh 分布式锁。"""
    try:
        await cache_delete(_refresh_lock_key(refresh_token))
    except Exception:
        pass


async def wait_for_cached_refresh_access_token(
    refresh_token: str,
    wait_ms: int = 1200,
    interval_ms: int = 80,
) -> Optional[str]:
    """等待并发中的 refresh 产出幂等结果。"""
    remaining = max(0, wait_ms)
    step = max(20, interval_ms)

    while remaining > 0:
        cached = await get_cached_refresh_access_token(refresh_token)
        if cached:
            return cached
        await asyncio.sleep(step / 1000)
        remaining -= step

    return await get_cached_refresh_access_token(refresh_token)


# ==================== Refresh Token 轮换 ====================
# 每次 /api/auth/refresh 都签发新的 refresh_token 并作废旧的。宽限期内，持旧 Token 的
# 并发 / 迟到请求（多标签页、首屏 bootstrap 与定时刷新撞车）复用同一次轮换结果，
# 不会互相挤掉登录态；宽限期过后旧 Token 再次出现，说明它被复制过，吊销该用户全部会话。
# Redis 不可用时与黑名单一致按放行处理：旧 Token 继续有效直至过期，不会误伤登录态。

REFRESH_ROTATION_GRACE_SECONDS = 60


def _refresh_rotated_key(token: str) -> str:
    return f"refresh:rotated:{_refresh_token_fingerprint(token)}"


def _refresh_retired_key(token: str) -> str:
    return f"refresh:retired:{_refresh_token_fingerprint(token)}"


def _remaining_lifetime_seconds(token: str) -> int:
    """Token 剩余有效期（秒）；解析失败时按完整有效期计"""
    try:
        exp = int(jwt.get_unverified_claims(token).get("exp", 0))
        return max(1, exp - int(time.time()))
    except Exception:
        return settings.refresh_token_expire_days * 24 * 60 * 60


async def get_rotated_refresh_pair(refresh_token: str) -> Optional[tuple[str, str]]:
    """宽限期内返回该 refresh_token 轮换得到的 (access_token, new_refresh_token)"""
    try:
        cached = await cache_get_bytes(_refresh_rotated_key(refresh_token))
        if not cached:
            return None
        payload = json.loads(cached.decode("utf-8"))
        access_token, new_refresh_token = payload.get("access"), payload.get("refresh")
    except Exception:
        return None
    if not access_token or not new_refresh_token:
        return None
    # 宽限期内若已登出或重置密码（新 Token 被拉黑 / 用户级吊销），不能借旧 Token 复活会话
    if await is_token_blacklisted(new_refresh_token):
        return None
    return access_token, new_refresh_token


async def wait_for_rotated_refresh_pair(
    refresh_token: str,
    wait_ms: int = 1200,
    interval_ms: int = 80,
) -> Optional[tuple[str, str]]:
    """等待并发中的轮换产出结果（与 wait_for_cached_refresh_access_token 同理）"""
    remaining = max(0, wait_ms)
    step = max(20, interval_ms)
    while remaining > 0:
        pair = await get_rotated_refresh_pair(refresh_token)
        if pair:
            return pair
        await asyncio.sleep(step / 1000)
        remaining -= step
    return await get_rotated_refresh_pair(refresh_token)


async def rotate_refresh_token(old_token: str, user: db_models.User) -> tuple[str, str]:
    """签发新的 access / refresh Token，并让旧 refresh_token 在宽限期后彻底失效"""
    claims = {"sub": user.username, "user_id": user.id}
    access_token = create_access_token(data=claims)
    new_refresh_token = create_refresh_token(data=claims)
    remaining = _remaining_lifetime_seconds(old_token)
    try:
        await cache_set_bytes(
            _refresh_rotated_key(old_token),
            json.dumps({"access": access_token, "refresh": new_refresh_token}).encode("utf-8"),
            ttl=REFRESH_ROTATION_GRACE_SECONDS,
        )
        await cache_set_bytes(_refresh_retired_key(old_token), b"1", ttl=remaining)
    except Exception as e:
        logger.warning(f"⚠️ 无法记录 Refresh Token 轮换: {e}")
    await blacklist_token(old_token, expires_in=remaining)
    return access_token, new_refresh_token


async def handle_refresh_token_reuse(refresh_token: str) -> None:
    """
    已轮换的旧 refresh_token 在宽限期后再次出现：判定为泄露，吊销该用户全部会话。

    退休标记按 Token 原文的指纹存放，只有真正签发过的 Token 才能命中，
    因此无法借伪造的 Token 去吊销别人的会话；登出拉黑等普通失效不会走到这里。
    """
    try:
        retired = await cache_get_bytes(_refresh_retired_key(refresh_token))
    except Exception:
        return
    if not retired:
        return
    try:
        payload = jwt.decode(
            refresh_token,
            settings.jwt_refresh_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},
        )
        user_id = int(payload.get("user_id"))
    except Exception:
        return
    logger.warning(f"⚠️ 检测到已轮换的 Refresh Token 被重复使用，吊销用户 {user_id} 的全部会话")
    await revoke_user_tokens(user_id)


def decode_token(token: str, token_type: str = "access") -> TokenData:
    """
    解码并验证Token
    
    Args:
        token: JWT Token字符串
        token_type: Token类型 (access/refresh)
        
    Returns:
        TokenData对象
        
    Raises:
        HTTPException: Token无效时抛出
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # 选择正确的密钥
        secret_key = settings.jwt_secret_key if token_type == "access" else settings.jwt_refresh_secret_key
        
        # 解码Token
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[settings.jwt_algorithm]
        )
        
        # 验证Token类型
        if payload.get("type") != token_type:
            raise credentials_exception
        
        username: str = payload.get("sub")
        user_id: int = payload.get("user_id")
        
        if username is None:
            raise credentials_exception
        
        return TokenData(username=username, user_id=user_id, issued_at=payload.get("iat"))
        
    except JWTError:
        raise credentials_exception


# ==================== 用户操作 ====================

async def get_user_by_email(db: AsyncSession, email: str) -> Optional[db_models.User]:
    """根据邮箱获取用户"""
    result = await db.execute(select(db_models.User).filter(db_models.User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_phone(db: AsyncSession, phone: str) -> Optional[db_models.User]:
    """根据手机号获取用户"""
    result = await db.execute(select(db_models.User).filter(db_models.User.phone == phone))
    return result.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[db_models.User]:
    """根据用户名获取用户"""
    result = await db.execute(select(db_models.User).filter(db_models.User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[db_models.User]:
    """根据ID获取用户"""
    result = await db.execute(select(db_models.User).filter(db_models.User.id == user_id))
    return result.scalar_one_or_none()


def _generate_numeric_code(length: int = 6) -> str:
    digits = string.digits
    return ''.join(secrets.choice(digits) for _ in range(length))

def _generate_invite_code(length: int = 8) -> str:
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


# ==================== 验证码防爆破 ====================
# 按（渠道, 目标, 用途）统计未成功的校验次数：优先 Redis（多 worker 共享），
# Redis 不可用时降级为进程内计数。每次校验先预占一次机会再比对（并发爆破同样受限），
# 连续 VERIFICATION_CODE_MAX_FAILURES 次未通过即作废该目标所有未使用的验证码，
# 必须重新获取验证码；验证成功时清零。

# 进程内降级计数：{key: (次数, 过期时间 monotonic)}
_verification_failures_fallback: dict[str, tuple[int, float]] = {}
_VERIFICATION_FAILURES_FALLBACK_MAX_KEYS = 10000


def _verification_failure_key(channel: str, target: str, purpose: str) -> str:
    normalized = f"{channel}:{purpose}:{(target or '').strip().lower()}"
    return f"auth:verify_fail:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


async def _get_verification_attempts(key: str) -> int:
    client = await get_redis_client()
    if client is not None:
        try:
            raw = await client.get(key)
            return int(raw or 0)
        except Exception as e:
            logger.warning(f"⚠️ Redis 读取验证码校验次数失败，降级到进程内计数: {e}")

    entry = _verification_failures_fallback.get(key)
    if not entry:
        return 0
    count, expires_at = entry
    if expires_at <= time.monotonic():
        _verification_failures_fallback.pop(key, None)
        return 0
    return count


async def _incr_verification_attempts(key: str) -> int:
    """次数 +1 并返回最新值；计数窗口与验证码有效期一致（每次顺延）"""
    ttl = VERIFICATION_CODE_TTL_MINUTES * 60
    client = await get_redis_client()
    if client is not None:
        try:
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl)
            results = await pipe.execute()
            return int(results[0])
        except Exception as e:
            logger.warning(f"⚠️ Redis 记录验证码校验次数失败，降级到进程内计数: {e}")

    now = time.monotonic()
    if len(_verification_failures_fallback) > _VERIFICATION_FAILURES_FALLBACK_MAX_KEYS:
        for stale_key in [k for k, (_, exp) in _verification_failures_fallback.items() if exp <= now]:
            _verification_failures_fallback.pop(stale_key, None)
    count, expires_at = _verification_failures_fallback.get(key, (0, 0.0))
    if expires_at <= now:
        count = 0
    count += 1
    _verification_failures_fallback[key] = (count, now + ttl)
    return count


async def _reset_verification_attempts(key: str) -> None:
    _verification_failures_fallback.pop(key, None)
    try:
        await cache_delete(key)
    except Exception:
        pass


async def _reserve_verification_attempt(key: str) -> int:
    """
    预占一次校验机会并返回这是第几次；超过上限直接拒绝（不再查库比对）。

    先计数、后比对：并发爆破时同样最多只有 VERIFICATION_CODE_MAX_FAILURES 次真正进入比对。
    """
    attempt = await _incr_verification_attempts(key)
    if attempt > VERIFICATION_CODE_MAX_FAILURES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码错误次数过多，请重新获取验证码"
        )
    return attempt


async def _handle_wrong_verification_code(db: AsyncSession, attempt: int, model, target_column, target: str, purpose: str) -> None:
    """
    输错时调用：用完最后一次机会则作废该目标+用途下所有未使用的验证码，并提示重新获取。

    作废操作必须立即提交：调用方随后会因本次报错回滚事务（此时会话内没有其他待提交的写入）。
    """
    if attempt < VERIFICATION_CODE_MAX_FAILURES:
        return

    now = datetime.now(timezone.utc)
    try:
        await db.execute(
            update(model)
            .where(
                target_column == target,
                model.purpose == purpose,
                model.is_used.isnot(True),
                model.expires_at > now,
            )
            .values(expires_at=now)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.warning(f"⚠️ 作废验证码失败: {e}")

    logger.warning("⚠️ 验证码连续输错 %s 次，已作废未使用的验证码: purpose=%s", attempt, purpose)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="验证码错误次数过多，请重新获取验证码"
    )


async def prepare_email_verification_code(
    db: AsyncSession,
    email: str,
    purpose: str = "register"
):
    """生成并暂存验证码（仅flush，调用方负责提交或回滚）"""
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(db_models.EmailVerificationCode)
        .filter(
            db_models.EmailVerificationCode.email == email,
            db_models.EmailVerificationCode.purpose == purpose
        )
        .order_by(db_models.EmailVerificationCode.created_at.desc())
        .limit(1)
    )
    latest = result.scalars().first()

    if latest:
        latest_created = latest.created_at
        if latest_created is not None and latest_created.tzinfo is None:
            latest_created = latest_created.replace(tzinfo=timezone.utc)
        elapsed = (now - latest_created).total_seconds()
        if elapsed < VERIFICATION_CODE_RESEND_SECONDS:
            remaining = int(VERIFICATION_CODE_RESEND_SECONDS - elapsed)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"验证码发送过于频繁，请在 {remaining} 秒后重试"
            )

    code = _generate_numeric_code()
    expires_at = now + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)

    record = db_models.EmailVerificationCode(
        email=email,
        code=code,
        purpose=purpose,
        expires_at=expires_at
    )

    db.add(record)
    await db.flush()

    # 因输错过多被锁定时，重新获取验证码即解除锁定（锁定时旧验证码已全部作废）
    fail_key = _verification_failure_key("email", email, purpose)
    if await _get_verification_attempts(fail_key) >= VERIFICATION_CODE_MAX_FAILURES:
        await _reset_verification_attempts(fail_key)

    return code, record


async def verify_email_code(
    db: AsyncSession,
    email: str,
    code: str,
    purpose: str = "register",
    auto_commit: bool = True
):
    now = datetime.now(timezone.utc)

    fail_key = _verification_failure_key("email", email, purpose)
    attempt = await _reserve_verification_attempt(fail_key)

    result = await db.execute(
        select(db_models.EmailVerificationCode)
        .filter(
            db_models.EmailVerificationCode.email == email,
            db_models.EmailVerificationCode.purpose == purpose,
            db_models.EmailVerificationCode.code == code
        )
        .order_by(db_models.EmailVerificationCode.created_at.desc())
        .limit(1)
    )

    record = result.scalars().first()

    if not record:
        await _handle_wrong_verification_code(
            db, attempt, db_models.EmailVerificationCode, db_models.EmailVerificationCode.email, email, purpose
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码错误")

    if record.is_used:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码已使用")

    record_expires = record.expires_at
    if record_expires is not None and record_expires.tzinfo is None:
        record_expires = record_expires.replace(tzinfo=timezone.utc)

    if record_expires < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码已过期")

    record.is_used = True
    record.verified_at = datetime.now(timezone.utc)
    if auto_commit:
        await db.commit()
    else:
        await db.flush()
    await _reset_verification_attempts(fail_key)
    return record


async def prepare_sms_verification_code(
    db: AsyncSession,
    phone: str,
    purpose: str = "register"
):
    """生成并暂存短信验证码（仅flush，调用方负责提交或回滚）"""
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(db_models.SmsVerificationCode)
        .filter(
            db_models.SmsVerificationCode.phone == phone,
            db_models.SmsVerificationCode.purpose == purpose
        )
        .order_by(db_models.SmsVerificationCode.created_at.desc())
        .limit(1)
    )
    latest = result.scalars().first()

    if latest:
        latest_created = latest.created_at
        if latest_created is not None and latest_created.tzinfo is None:
            latest_created = latest_created.replace(tzinfo=timezone.utc)
        elapsed = (now - latest_created).total_seconds()
        if elapsed < VERIFICATION_CODE_RESEND_SECONDS:
            remaining = int(VERIFICATION_CODE_RESEND_SECONDS - elapsed)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"验证码发送过于频繁，请在 {remaining} 秒后重试"
            )

    code = _generate_numeric_code()
    expires_at = now + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)

    record = db_models.SmsVerificationCode(
        phone=phone,
        code=code,
        purpose=purpose,
        expires_at=expires_at
    )

    db.add(record)
    await db.flush()

    # 因输错过多被锁定时，重新获取验证码即解除锁定（锁定时旧验证码已全部作废）
    fail_key = _verification_failure_key("sms", phone, purpose)
    if await _get_verification_attempts(fail_key) >= VERIFICATION_CODE_MAX_FAILURES:
        await _reset_verification_attempts(fail_key)

    return code, record


async def verify_sms_code(
    db: AsyncSession,
    phone: str,
    code: str,
    purpose: str = "register",
    auto_commit: bool = True
):
    now = datetime.now(timezone.utc)

    fail_key = _verification_failure_key("sms", phone, purpose)
    attempt = await _reserve_verification_attempt(fail_key)

    result = await db.execute(
        select(db_models.SmsVerificationCode)
        .filter(
            db_models.SmsVerificationCode.phone == phone,
            db_models.SmsVerificationCode.purpose == purpose,
            db_models.SmsVerificationCode.code == code
        )
        .order_by(db_models.SmsVerificationCode.created_at.desc())
        .limit(1)
    )

    record = result.scalars().first()

    if not record:
        await _handle_wrong_verification_code(
            db, attempt, db_models.SmsVerificationCode, db_models.SmsVerificationCode.phone, phone, purpose
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码错误")

    if record.is_used:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码已使用")

    record_expires = record.expires_at
    if record_expires is not None and record_expires.tzinfo is None:
        record_expires = record_expires.replace(tzinfo=timezone.utc)

    if record_expires < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码已过期")

    record.is_used = True
    record.verified_at = datetime.now(timezone.utc)
    if auto_commit:
        await db.commit()
    else:
        await db.flush()
    await _reset_verification_attempts(fail_key)
    return record


async def create_user(db: AsyncSession, user: UserCreate) -> db_models.User:
    """
    创建新用户
    
    Args:
        db: 数据库会话
        user: 用户创建数据
        
    Returns:
        创建的用户对象
        
    Raises:
        HTTPException: 用户已存在时抛出
    """
    # 检查手机号是否已存在
    if user.phone and await get_user_by_phone(db, user.phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该手机号已被注册"
        )

    resolved_email = user.email or f"{user.phone}@phone.uninotebook.local"

    # 检查邮箱是否已存在
    if await get_user_by_email(db, resolved_email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该邮箱已被注册"
        )
    
    if len(user.password.encode('utf-8')) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="密码过长，请控制在128字符以内"
        )
    
    # 生成专属邀请码
    my_invite_code = _generate_invite_code(8)
    
    # 创建用户
    db_user = db_models.User(
        email=resolved_email,
        phone=user.phone,
        username=user.username,
        hashed_password=get_password_hash(user.password),
        full_name=user.full_name,
        my_invite_code=my_invite_code,
        invited_by_id=user.invited_by_id
    )
    
    db.add(db_user)
    await db.flush()  # 先 flush 获取 user_id，再初始化积分
    
    # 🆕 初始化用户积分（赠送注册奖励）
    from credit_service import CreditService
    credit_service = CreditService(db)
    await credit_service.initialize_new_user(db_user.id)
    
    # 如果有邀请人，给邀请人和被邀请人发放奖励
    if user.invited_by_id:
        await credit_service.process_invite_reward(user.invited_by_id, db_user.id)
    
    await db.commit()
    await db.refresh(db_user)
    
    logger.info(f"✅ 新用户注册: {user.username} ({user.phone or resolved_email})")

    # 新用户默认带几篇示例文档；失败不能影响注册（用独立会话，不干扰本次请求的 db）
    try:
        from runtime.samples import seed_sample_documents
        await seed_sample_documents(db_user.id)
    except Exception as exc:
        logger.warning(f"⚠️ 写入示例文档失败 (user_id={db_user.id}): {exc}")

    return db_user


async def authenticate_user(db: AsyncSession, username: str, password: str) -> Optional[db_models.User]:
    """
    验证用户
    
    Args:
        db: 数据库会话
        username: 邮箱 (OAuth2标准字段名为username)
        password: 密码
        
    Returns:
        验证成功返回用户对象，失败返回None
    """
    # 兼容手机号/邮箱登录
    account = (username or '').strip()
    user = None

    if '@' in account:
        user = await get_user_by_email(db, account)
    else:
        try:
            phone = normalize_cn_phone(account)
            user = await get_user_by_phone(db, phone)
        except ValueError:
            user = await get_user_by_email(db, account)
    
    # 用户不存在或密码错误
    if not user or not verify_password(password, user.hashed_password):
        return None
    
    # 检查用户是否激活
    if not user.is_active:
        return None
    
    # 更新最后登录时间
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    
    return user


# ==================== 依赖注入 ====================

async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> db_models.User:
    """
    获取当前登录用户
    
    仅从 Authorization Header 获取 Access Token
    """
    access_token = token
        
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或Token已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # 解码Token
    token_data = decode_token(access_token, token_type="access")
    
    # 重置密码后，此前签发的 Token 一律失效
    if await is_user_token_revoked(token_data.user_id, token_data.issued_at):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录状态已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 获取用户
    user = await get_user_by_id(db, token_data.user_id)
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户已被禁用"
        )
    
    return user


async def get_current_active_superuser(
    current_user: db_models.User = Depends(get_current_user)
) -> db_models.User:
    """
    获取当前超级管理员
    
    用于保护需要管理员权限的路由
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足"
        )
    return current_user


# ==================== 启动安全校验 ====================

MIN_JWT_SECRET_LENGTH = 32
# 公开模板 / 文档里出现过的占位写法
_WEAK_SECRET_MARKERS = (
    "change-me", "changeme", "change-in-production", "please-change",
    "your-super-secret", "your-refresh-secret",
)


def _is_weak_jwt_secret(value: Optional[str], default: Optional[str]) -> bool:
    secret = (value or "").strip()
    if len(secret) < MIN_JWT_SECRET_LENGTH:
        return True
    if default and secret == default:
        return True
    lowered = secret.lower()
    return any(marker in lowered for marker in _WEAK_SECRET_MARKERS)


def validate_security_settings() -> None:
    """
    启动时校验 JWT 密钥（应在应用启动时调用一次）。

    - SaaS + 生产环境（APP_ENV=production）：任一 JWT 密钥仍为占位/默认值或短于 32 位 → 抛出 RuntimeError，拒绝启动
    - SaaS + 非生产环境：仅告警
    - 本地模式：不签发 Token，直接跳过
    """
    if settings.is_local_mode:
        return

    from config import ENVIRONMENT

    fields = type(settings).model_fields
    weak = [
        name.upper()
        for name in ("jwt_secret_key", "jwt_refresh_secret_key")
        if _is_weak_jwt_secret(getattr(settings, name, None), fields[name].default if name in fields else None)
    ]
    if not weak:
        return

    message = (
        f"JWT 密钥不安全（{', '.join(weak)}）：仍为占位/默认值或长度不足 {MIN_JWT_SECRET_LENGTH} 位。"
        "请通过环境变量或 .env 设置随机密钥，例如 python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    )
    if ENVIRONMENT == "production":
        raise RuntimeError(message)
    logger.warning("⚠️ %s", message)
