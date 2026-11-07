import asyncio
from database import AsyncSessionLocal
from db_models import InviteCode
from sqlalchemy import select
from utils.logger import get_logger
logger = get_logger(__name__)

# 30个一次性邀请码
ONE_TIME_CODES = [
    "8ZZ9RSBQIL", "CANTRGUEC8", "XVFT4VB29G", "QHY9RDYSVJ", "0CHRMFQ2XZ",
    "GN8C6IR3D0", "MTHXV0IBGR", "QDEA9UKPV6", "WYJ951FNOP", "FEC1KZVTK8",
    "IABUTGH5KL", "4M7HB0HPNI", "9SBXSBL49A", "4YY7IKMVK1", "2TSHKGH72X",
    "LF018F2EF2", "GIEL1EZYHS", "HBXQH9VBOT", "YHJD868JSM", "FWMWHVF91T",
    "PPALBYU420", "7629AAF3HW", "QE0LUA91RN", "E1C6GQ350T", "9ORBTK3YQO",
    "BTFRMQ9S7D", "90O3H5742D", "LC26RF3HJ1", "0U52C7NHNY", "J5VH01M4PY"
]

async def init_invite_codes():
    async with AsyncSessionLocal() as db:
        logger.info("🚀 开始初始化邀请码...")
        
        # 1. 初始化/更新 SUFEMAX
        result = await db.execute(select(InviteCode).filter(InviteCode.code == "SUFEMAX"))
        sufemax = result.scalars().first()
        
        if sufemax:
            logger.info("🔄 更新 SUFEMAX: 剩余可用 5 次")
            sufemax.max_uses = sufemax.used_count + 5  # 现有已用 + 5 = 还能用5次
        else:
            logger.info("✅ 创建 SUFEMAX: 可用 5 次")
            sufemax = InviteCode(code="SUFEMAX", max_uses=5)
            db.add(sufemax)
            
        # 2. 批量创建一次性邀请码
        added_count = 0
        for code in ONE_TIME_CODES:
            result = await db.execute(select(InviteCode).filter(InviteCode.code == code))
            if not result.scalars().first():
                new_code = InviteCode(code=code, max_uses=1)
                db.add(new_code)
                added_count += 1
        
        if added_count > 0:
            logger.info(f"✅ 新增 {added_count} 个一次性邀请码")
        else:
            logger.info("ℹ️ 所有一次性邀请码已存在")
            
        await db.commit()
        logger.info("✨ 初始化完成！")

if __name__ == "__main__":
    asyncio.run(init_invite_codes())
