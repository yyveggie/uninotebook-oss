"""
计费层

本地部署下用户直接用自己的 API Key 调用模型，费用结算给模型服务商，因此不存在额度扣减。

接缝设计：全仓 43 处调用点统一写作 `CreditService(db)`，因此只需在
`credit_service/__init__.py` 的**导出层**按模式换类，调用点一行不动。

空实现必须覆盖额度服务的全部公开方法，并返回结构等价的中性值——
否则调用方解包返回值时会炸。余额恒为一个哨兵大数，扣费恒成功且不落库。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import db_models
from utils.logger import get_logger

logger = get_logger(__name__)


# 本地模式的哨兵余额：足够大以致任何余额检查都通过，
# 又不至于大到在前端格式化时显示成科学计数法。
UNLIMITED_BALANCE = 999_999_999


class NullCreditService:
    """
    无额度扣减的空实现（本地部署）。

    与 `CreditService` 保持**相同的方法签名与返回结构**，但：
    - 所有扣费/退款为空操作，不写数据库
    - 余额查询恒返回 UNLIMITED_BALANCE
    - 余额充足性检查恒为 True，永不抛 InsufficientCreditsError
    """

    def __init__(self, db=None):
        self.db = db

    # ==================== 查询 ====================

    async def get_user_credits(self, user_id: int, for_update: bool = False) -> db_models.UserCredit:
        """返回一个**游离态**（未加入 session）的积分对象，仅供读取展示"""
        return db_models.UserCredit(
            user_id=user_id,
            balance=UNLIMITED_BALANCE,
            total_earned=UNLIMITED_BALANCE,
            total_spent=0,
        )

    async def get_balance(self, user_id: int) -> int:
        return UNLIMITED_BALANCE

    async def check_sufficient(self, user_id: int, amount: int) -> bool:
        return True

    # ==================== 增减 ====================

    async def add_credits(
        self,
        user_id: int,
        amount: int,
        transaction_type: str,
        description: str = None,
        reference_id: str = None,
    ) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, amount

    async def deduct_credits(
        self,
        user_id: int,
        amount: int,
        transaction_type: str,
        description: str = None,
        reference_id: str = None,
        check_balance: bool = True,
    ) -> Tuple[int, int]:
        # 本地模式不计费：不校验余额、不落流水、不抛异常
        return UNLIMITED_BALANCE, 0

    async def refund_credits(
        self,
        user_id: int,
        amount: int,
        original_transaction_type: str,
        reason: str = None,
        reference_id: str = None,
    ) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    # ==================== 账户生命周期 ====================

    async def initialize_new_user(self, user_id: int) -> int:
        return UNLIMITED_BALANCE

    async def process_invite_reward(self, inviter_id: int, invitee_id: int) -> None:
        return None

    # ==================== 签到 / 充值（本地版无意义，返回中性值） ====================

    async def daily_checkin(self, user_id: int) -> Dict[str, Any]:
        return {
            "success": False,
            "message": "本地版无积分体系",
            "credits_earned": 0,
            "balance": UNLIMITED_BALANCE,
        }

    async def get_checkin_status(self, user_id: int) -> Dict[str, Any]:
        return {
            "checked_in_today": False,
            "checkin_time": None,
            "credits_earned": 0,
        }

    async def create_recharge_order(self, *args, **kwargs):
        raise NotImplementedError("本地版不支持充值")

    async def complete_recharge(self, order_no: str) -> Dict[str, Any]:
        raise NotImplementedError("本地版不支持充值")

    async def get_transaction_history(
        self,
        user_id: int,
        limit: int = 20,
        offset: int = 0,
        transaction_type: str = None,
    ) -> Tuple[List[db_models.CreditTransaction], int]:
        return [], 0

    # ==================== 便捷扣费方法（全部空操作） ====================

    async def deduct_for_qa(
        self,
        user_id: int,
        response_length: int = 0,
        tool_call_count: int = 0,
        reference_id: str = None,
    ) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_document(self, user_id: int, document_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_flashcard(self, user_id: int, document_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_quiz(self, user_id: int, document_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_mindmap(self, user_id: int, document_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_highlights(self, user_id: int, document_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_report(self, user_id: int, document_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_polish(self, user_id: int, mode: str = "general") -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_interview(self, user_id: int, document_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_ppt(self, user_id: int, document_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_poster(self, user_id: int, document_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_podcast(self, user_id: int, document_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_deep_research(self, user_id: int, document_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_ai_organize(self, user_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0

    async def deduct_for_ai_eureka(self, user_id: int) -> Tuple[int, int]:
        return UNLIMITED_BALANCE, 0
