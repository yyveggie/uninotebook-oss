"""
配置文件
"""
import os
from pathlib import Path
from typing import Optional, List
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from utils.logger import get_logger
logger = get_logger(__name__)


ENVIRONMENT = os.getenv('APP_ENV', 'development').lower()
_CONFIG_DIR = Path(__file__).resolve().parent
_DEFAULT_ENV_FILE = _CONFIG_DIR / '.env'
_ENV_FILE_CANDIDATE = _CONFIG_DIR / f'.env.{ENVIRONMENT}'
_ACTIVE_ENV_FILE = _ENV_FILE_CANDIDATE if _ENV_FILE_CANDIDATE.exists() else _DEFAULT_ENV_FILE


class Settings(BaseSettings):
    """应用配置"""

    # 部署模式
    # local  : 单用户本地部署 —— 免登录、SQLite、进程内任务、用户自填 API Key
    # 其他值 : 多用户服务端部署 —— 真实鉴权、PostgreSQL、Redis、Celery
    deploy_mode: str = "saas"

    # 本地版数据目录（凭据、SQLite、向量库等落盘位置）
    local_data_dir: Optional[str] = None
    default_chat_provider: Optional[str] = None
    embedding_dimensions: Optional[int] = Field(default=None, ge=1, le=65536)
    local_embedding_concurrency: int = 4
    local_task_workers: int = Field(default=4, ge=1, le=64)
    # 文档解析（上传 / 网页导入 / 添加来源）专用线程数，不与 PPT、播客等长任务争抢
    local_document_task_workers: int = Field(default=2, ge=1, le=16)
    local_task_queue_limit: int = Field(default=100, ge=1, le=10000)
    local_task_timeout_seconds: float = Field(default=900, gt=0)
    # 本地版额外放行的 Host（逗号分隔）。本地版免登录，默认只接受 localhost / 127.0.0.1
    # 的 Host 头，以阻断 DNS 重绑定；需要局域网访问时在此显式列出主机名或 IP。
    local_allowed_hosts: str = ""

    # 单个上传文件的大小上限（MB）
    max_upload_mb: int = Field(default=50, ge=1, le=2048)
    # 新用户的示例文档：本地版在空数据目录首次启动时写入，线上版在注册时写入
    sample_documents_enabled: bool = True

    # OpenAI配置
    # 注：本地部署启动时用户可能尚未填写任何 Key，故此处允许为空。
    openai_api_key: Optional[str] = None
    openai_model: str = "qwen3-max"  # 支持结构化输出的模型
    openai_base_url: Optional[str] = None  # 自定义API地址
    openai_max_preview_model: str = "qwen3-max-preview"
    openai_max_output_tokens: int = 8192
    
    # 通义千问配置（备用）
    qwen_api_key: Optional[str] = None
    qwen_model: Optional[str] = "qwen3-max"
    # 本地版「通义千问」卡片的默认端点（百炼兼容模式）；托管版的 Qwen 走 openai_* 配置
    qwen_base_url: Optional[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # Image Generation Configuration (DashScope qwen-image-max)
    image_gen_api_key: Optional[str] = None
    image_gen_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    image_gen_model: str = "qwen-image-max"
    
    # 阿里云百炼配置（Embeddings + Chat）
    dashscope_api_key: Optional[str] = None
    dashscope_native_api_key: Optional[str] = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_embedding_model: str = "text-embedding-v4"
    
    # Moonshot (Kimi) 配置
    moonshot_api_key: Optional[str] = None
    moonshot_base_url: str = "https://api.moonshot.cn/v1"
    moonshot_model: str = "kimi-k2.6"
    moonshot_thinking_model: str = "kimi-k2.6"
    moonshot_thinking_turbo_model: str = "kimi-k2.6"

    # DeepSeek 配置
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    deepseek_reasoner_model: str = "deepseek-reasoner"
    
    # DeepSeek Speciale 配置
    deepseek_speciale_base_url: str = "https://api.deepseek.com/v3.2_speciale_expires_on_20251215/v1"
    deepseek_speciale_model: str = "deepseek-reasoner"
    
    # API配置
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_base_url: str = "http://localhost:8000"  # 实际部署由 .env 的 API_BASE_URL 覆盖
    
    # CORS配置
    cors_origins: str = "http://localhost:3000,http://localhost:5173"  # 实际部署由 .env 的 CORS_ORIGINS 覆盖
    
    # JWT配置
    jwt_secret_key: str = "your-super-secret-key-please-change-in-production-min-32-chars"
    jwt_refresh_secret_key: str = "your-refresh-secret-key-please-change-in-production-min-32-chars"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15  # Access Token 15分钟过期
    refresh_token_expire_days: int = 7  # Refresh Token 7天过期

    # 认证注册策略
    # auto: 按环境自动（production=仅邮箱，其他=邮箱+手机号）
    # email_only: 仅邮箱注册
    # email_phone: 邮箱+手机号注册
    auth_register_mode: str = "auto"
    auth_cookie_domain: Optional[str] = None
    auth_cookie_secure: Optional[bool] = None
    auth_cookie_samesite: str = "lax"
    
    # 数据库配置
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"  # 实际部署由 .env 的 DB_USER 覆盖
    db_password: str = ""  # 默认为空（本地开发）
    db_name: str = "uninotebook"  # 实际部署由 .env 的 DB_NAME 覆盖
    db_auto_init: bool = ENVIRONMENT != "production"

    # 数据库连接池治理（支持按连接预算反推）
    db_connection_budget: int = 40  # 单实例总连接预算（所有 Gunicorn workers 合计）
    db_workers_hint: int = 3  # Gunicorn worker 预期值（用于反推每 worker 连接池）
    db_pool_size: Optional[int] = None  # 每 worker 固定池大小（设置后优先）
    db_max_overflow: Optional[int] = None  # 每 worker 固定溢出连接（设置后优先）
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    db_pool_pre_ping: bool = True
    db_pool_use_lifo: bool = True

    # PgBouncer 兼容开关（transaction pooling 建议关闭 asyncpg statement cache）
    db_use_pgbouncer: bool = False

    # 邮件服务配置（用于发送验证码）
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_sender: Optional[str] = None

    # 文档解析策略：simple | hybrid
    document_parser_strategy: str = "simple"
    hybrid_extract_tables: bool = True
    hybrid_extract_figures: bool = True
    hybrid_extract_formulas: bool = True

    # 报告生成上下文策略
    report_context_max_chars: int = 20000
    report_context_ratio_head: float = 0.34
    report_context_ratio_mid: float = 0.33
    report_context_ratio_tail: float = 0.33

    # Redis 缓存
    redis_enabled: bool = True
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 24 * 60 * 60

    # Celery 并发舱壁与幂等配置
    celery_generation_queue_max_pending: int = 200
    celery_generation_heavy_queue_max_pending: int = 80
    celery_generation_chat_queue_max_pending: int = 300
    celery_generation_content_queue_max_pending: int = 150
    celery_generation_creative_queue_max_pending: int = 60
    celery_task_expires_seconds: int = 900
    idempotency_ttl_seconds: int = 600
    
    # ChromaDB (Vector Store) - 可选：独立服务模式
    chroma_server_host: Optional[str] = None  # e.g., "localhost" or "chroma-server"
    chroma_server_port: int = 8100
    chroma_server_ssl: bool = False

    # Tencent COS Configuration
    tencent_cos_secret_id: Optional[str] = None
    tencent_cos_secret_key: Optional[str] = None
    tencent_cos_region: Optional[str] = None
    tencent_cos_bucket: Optional[str] = None

    # Podcast TTS Voice Configuration
    # Host(A) 建议女声，Expert(B) 建议男声
    podcast_voice_host: str = "sambert-zhiwei-v1"
    podcast_voice_expert: str = "sambert-zhichu-v1"

    # Tencent SMS Configuration
    tencent_sms_secret_id: Optional[str] = None
    tencent_sms_secret_key: Optional[str] = None
    tencent_sms_region: str = "ap-guangzhou"
    tencent_sms_sdk_app_id: Optional[str] = None
    tencent_sms_sign_name: Optional[str] = None
    tencent_sms_template_id_register: Optional[str] = None
    tencent_sms_template_id_reset_password: Optional[str] = None
    
    @field_validator("embedding_dimensions", mode="before")
    @classmethod
    def optional_dimensions(cls, value):
        return None if value == "" else value

    def model_post_init(self, __context) -> None:
        """本地版的默认值调整（用户显式配置的一律不覆盖）"""
        if not self.is_local_mode:
            return

        explicit = self.model_fields_set

        # 本地版不跑 Redis：任务已改为进程内执行，缓存链路本就支持降级
        if "redis_enabled" not in explicit:
            self.redis_enabled = False

        # SQLite 由 create_all 建表，必须允许自动建表，否则首次启动是空库
        if "db_auto_init" not in explicit:
            self.db_auto_init = True

        # 本地访问放开常见前端端口
        if "cors_origins" not in explicit:
            self.cors_origins = (
                "http://localhost:5173,http://127.0.0.1:5173,"
                "http://localhost:8000,http://127.0.0.1:8000"
            )

    @property
    def cors_origins_list(self) -> List[str]:
        """将CORS origins字符串转换为列表"""
        if isinstance(self.cors_origins, str):
            return [origin.strip() for origin in self.cors_origins.split(",")]
        return self.cors_origins

    @property
    def is_local_mode(self) -> bool:
        """是否为开源本地版"""
        return str(self.deploy_mode).strip().lower() == "local"

    @property
    def is_saas_mode(self) -> bool:
        """是否为托管 SaaS 版（默认）"""
        return not self.is_local_mode

    @property
    def data_dir(self) -> Path:
        """本地数据目录（凭据 / SQLite / 向量库）"""
        if self.local_data_dir:
            path = Path(self.local_data_dir).expanduser()
        else:
            path = Path.home() / ".uninotebook"
        return path


    model_config = {
        "env_file": str(_ACTIVE_ENV_FILE),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }


settings = Settings()
