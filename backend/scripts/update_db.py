import asyncio
from database import init_db
import db_models # Import to register models

async def main():
    await init_db()

if __name__ == "__main__":
    asyncio.run(main())
