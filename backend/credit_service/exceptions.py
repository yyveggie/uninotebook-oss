# credit_service/exceptions.py
"""
积分系统自定义异常
"""


class CreditOperationError(Exception):
    """积分操作通用异常"""
    def __init__(self, message: str = "积分操作失败"):
        self.message = message
        super().__init__(self.message)


class OrderAlreadyPaidError(CreditOperationError):
    """充值订单已支付（重复回调），调用方应按幂等成功处理"""
    pass


class InsufficientCreditsError(CreditOperationError):
    """积分不足异常"""
    def __init__(
        self, 
        required: int, 
        available: int, 
        message: str = None
    ):
        self.required = required
        self.available = available
        self.message = message or f"积分不足：需要 {required} 积分，当前余额 {available} 积分"
        super().__init__(self.message)
