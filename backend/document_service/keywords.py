import json
from typing import List
from openai import AsyncOpenAI
from config import settings
from utils.logger import get_logger
logger = get_logger(__name__)

class KeywordExtractor:
    """
    LLM-based Keyword Extractor
    Extracts high-quality search keywords from user queries or document context.
    """
    
    def __init__(self):
        try:
            from runtime.credentials import credential_store
            deepseek_key = credential_store.api_key("deepseek") or settings.deepseek_api_key
            openai_key = credential_store.api_key("openai") or settings.openai_api_key

            if settings.is_local_mode:
                from llm_service import get_model_config
                profile = get_model_config("deepseek")
                api_key, base_url, self.model = profile["api_key"], profile["base_url"], profile["model"]
            elif deepseek_key:
                api_key = deepseek_key
                base_url = credential_store.base_url("deepseek") or settings.deepseek_base_url or "https://api.deepseek.com/v1"
                self.model = settings.deepseek_model
            elif openai_key:
                api_key = openai_key
                base_url = credential_store.base_url("openai") or settings.openai_base_url
                self.model = settings.openai_model
            else:
                api_key = None
                base_url = None
                self.model = None

            if api_key:
                kwargs = {"api_key": api_key}
                if base_url:
                    kwargs["base_url"] = base_url
                self.client = AsyncOpenAI(**kwargs)
            else:
                self.client = None
        except Exception as e:
            logger.warning(f"⚠️ KeywordExtractor initialization failed: {e}")
            self.client = None

    async def extract_keywords(self, query: str, context: str = None) -> List[str]:
        """
        Extract 3-5 distinct search keywords from query.
        """
        if not self.client:
            return self._fallback_keywords(query)
            
        try:
            prompt = f"""
            Task: Extract 3-5 diverse, high-value search keywords/phrases from the Query.
            Context: The user is searching in a knowledge base of academic/technical documents.
            
            Rules:
            1. Output ONLY a JSON array of strings. No markdown, no explanations.
            2. Keywords should be specific entities, concepts, or technical terms.
            3. Remove stop words and conversational filler.
            4. If the query is in Chinese, include both Chinese keywords and their English technical translations if applicable.
            5. If context is provided, use it to disambiguate.

            Query: {query}
            {f"Context: {context}" if context else ""}
            
            Example Output: ["Transformer", "Self-Attention", "NLP", "注意力机制"]
            """
            
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, # Low temp for consistency
                max_tokens=100
            )
            
            content = response.choices[0].message.content.strip()
            # Handle potential markdown code blocks
            if content.startswith("```"):
                content = content.split("```")[1].strip()
                if content.startswith("json"):
                    content = content[4:].strip()
            
            keywords = json.loads(content)
            
            # Basic validation
            if isinstance(keywords, list):
                logger.info(f"✨ Extracted Keywords: {keywords}")
                return keywords[:5]
            else:
                return self._fallback_keywords(query)
                
        except Exception as e:
            logger.error(f"❌ Keyword extraction failed: {e}")
            return self._fallback_keywords(query)

    def _fallback_keywords(self, query: str) -> List[str]:
        """Simple splitting as fallback"""
        # Remove common punctuation
        for char in ".,?!;:\"'()[]{}":
            query = query.replace(char, "")
        return [w.strip() for w in query.split() if len(w.strip()) > 1][:5]
