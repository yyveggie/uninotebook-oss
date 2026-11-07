import os
import sys

from config import settings
from document_service.engines.paper2slides.main import main

if __name__ == "__main__":
    # 为 Kimi 注入默认环境变量（已显式设置的不覆盖）
    #
    # ⚠️ 此处**禁止硬编码 API Key**。
    # Key 一律来自环境变量或 .env（经 settings 读取）。
    if not os.getenv("RAG_LLM_API_KEY"):
        api_key = settings.moonshot_api_key
        if not api_key:
            sys.exit(
                "缺少 API Key：请设置环境变量 RAG_LLM_API_KEY，"
                "或在 .env 中配置 MOONSHOT_API_KEY"
            )
        os.environ["RAG_LLM_API_KEY"] = api_key
    if not os.getenv("RAG_LLM_BASE_URL"):
        os.environ["RAG_LLM_BASE_URL"] = settings.moonshot_base_url
    if not os.getenv("LLM_MODEL"):
        os.environ["LLM_MODEL"] = settings.moonshot_model
    if not os.getenv("MAX_OUTPUT_TOKENS"):
        os.environ["MAX_OUTPUT_TOKENS"] = str(settings.openai_max_output_tokens)

    main()
