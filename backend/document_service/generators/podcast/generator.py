
import json
import os
import time
import logging
import asyncio
from typing import List, Dict, Optional, Any
from pathlib import Path
from langchain_core.messages import SystemMessage, HumanMessage

from ..base import BaseGenerator
from db_models import DocumentPodcast
from config import settings
from utils.cos_service import COSService
from runtime.paths import data_path

logger = logging.getLogger(__name__)

class PodcastGenerator(BaseGenerator):
    """
    AI Podcast 生成器
    使用 LLM 生成对话脚本，使用 CosyVoice/Sambert 生成语音
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from runtime.credentials import credential_store

        self.tts_api_key = credential_store.api_key("dashscope_native")

    async def generate_podcast(self, task_id: int, text: str, style: str = "fun", language: str = "zh") -> Optional[str]:
        """
        执行完整的播客生成流程
        1. Generate Script
        2. Generate Audio (Parallel)
        3. Merge Audio

        Returns:
            None 表示成功；失败时返回错误信息（记录已标记为 failed），供调用方退款
        """
        from runtime.features import require_capability

        require_capability("podcast")
        try:
            # 0. 更新状态: Processing
            await self._update_status(task_id, "processing", 10, "generating_script")
            
            # 1. 生成脚本
            logger.info(f"Generating podcast script for task {task_id}...")
            script = await self._generate_script(text, style, language)
            
            if not script:
                raise ValueError("Failed to generate script")
                
            # 保存脚本到数据库
            await self._update_script(task_id, script)
            await self._update_status(task_id, "processing", 30, "generating_audio")
            
            # 2. 生成音频
            logger.info(f"Generating audio segments for task {task_id}...")
            temp_dir = data_path("temp", f"podcast_{task_id}_{int(time.time())}")
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            audio_segments = await self._generate_audio_segments(script, temp_dir)
            await self._update_status(task_id, "processing", 80, "merging_audio")
            
            # 3. 合并音频
            logger.info(f"Merging audio for task {task_id}...")
            final_audio_path = str(data_path("podcasts", f"podcast_{task_id}.mp3"))
            data_path("podcasts").mkdir(parents=True, exist_ok=True)
            
            self._merge_audio(audio_segments, final_audio_path)

            # 3.1 上传到 COS（优先），失败则回退本地路径
            final_audio_ref = final_audio_path
            cos_upload_error = None
            final_stage = "completed"
            try:
                cos = COSService()
                cos_key = f"podcasts/podcast_{task_id}.mp3"
                cos_url, upload_error = cos.upload_file_verbose(final_audio_path, cos_key)
                if cos_url:
                    final_audio_ref = cos_url
                    final_stage = "completed_cos"
                    try:
                        os.remove(final_audio_path)
                    except Exception:
                        pass
                else:
                    final_stage = "completed_local_fallback"
                    cos_upload_error = upload_error or "COS upload returned empty url"
            except Exception as upload_err:
                final_stage = "completed_local_fallback"
                cos_upload_error = f"{type(upload_err).__name__}: {upload_err}"
                logger.warning(f"COS upload failed for podcast {task_id}: {cos_upload_error}")
            
            # 4. 完成
            await self._update_status(task_id, "completed", 100, final_stage, final_audio_ref, cos_upload_error)
            
            # 清理临时文件
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            return None

        except Exception as e:
            logger.error(f"Podcast generation failed: {e}", exc_info=True)
            await self._update_status(task_id, "failed", 0, "failed", error=str(e))
            return str(e) or type(e).__name__

    async def _generate_script(self, text: str, style: str, language: str) -> List[Dict]:
        """
        使用 LLM 生成双人对话脚本
        style: fun(轻松有趣) / serious(专业深度) / summary(简明摘要)
        """

        # ---- 风格差异化配置 ----
        STYLE_CONFIG = {
            "fun": {
                "role_a": "充满好奇心、活力四射的主持人，善于用生活化的类比和幽默感让复杂概念变得通俗易懂，经常发出感叹和追问。声音风格：轻松活泼。",
                "role_b": "知识渊博但亲和力极强的专家，喜欢用故事和例子解释问题，偶尔开玩笑。声音风格：温暖随和。",
                "tone": '轻松愉快、像两个好朋友聊天。大量使用口语化表达和填充词（如"哇"、"真的吗"、"有意思"、"对对对"等）。可以适当跑题或加入有趣的类比，让听众感觉在听一场有趣的闲聊。',
                "length": "目标时长约 5-8 分钟（约 1000-1500 字）。",
                "structure": '    - 开场：轻松地欢迎听众，用一个有趣的引子引入主题。\n    - 主体：围绕关键点展开对话，主持人用"小白视角"提问，专家深入浅出地回答，穿插类比和故事。\n    - 结尾：轻松总结要点，留下思考或彩蛋。',
            },
            "serious": {
                "role_a": "严谨专业的学术主持人，善于提出有深度的问题，追问细节和逻辑链条，引导讨论层层递进。声音风格：沉稳干练。",
                "role_b": "该领域的资深专家，回答精准有条理，善于引用数据、研究和理论框架来支撑观点。声音风格：权威专业。",
                "tone": "严肃专业、学术氛围。语言正式但不生硬，减少口语填充词，注重逻辑性和准确性。每个观点都应有充分的论证和解释。",
                "length": "目标时长约 8-12 分钟（约 1500-2500 字）。对话应更深入、更详细。",
                "structure": "    - 开场：正式介绍主题背景和讨论目的。\n    - 主体：系统性地讨论核心概念，逐层深入。主持人追问原理、机制和影响，专家给出详细的专业解答。讨论应涵盖：概念定义、核心原理、实际应用、局限性或争议。\n    - 结尾：系统总结关键结论，指出未来方向或开放问题。",
            },
            "summary": {
                "role_a": "高效的主持人，善于抓住重点，快节奏地推进话题，确保每个要点都被简明扼要地覆盖。声音风格：清晰明快。",
                "role_b": "善于提炼和概括的专家，回答简洁有力，每次发言都直击要害，不做冗余展开。声音风格：干脆利落。",
                "tone": '简洁高效、直奔主题。避免冗长的铺垫和过多的填充词，每一句话都有信息量。像是一份"有声笔记"，帮助听众快速掌握核心内容。',
                "length": "目标时长约 3-5 分钟（约 500-800 字）。精简为最关键的要点。",
                "structure": "    - 开场：一句话引入主题，立刻进入正文。\n    - 主体：快速列举并解释 3-5 个核心要点，主持人引出，专家精炼作答。\n    - 结尾：用 1-2 句话概括全文核心。",
            },
        }

        cfg = STYLE_CONFIG.get(style, STYLE_CONFIG["fun"])

        system_prompt = """你是一位专业的播客制作人。你的目标是将给定的内容转化为一段自然的双人播客对话。

**角色设定：**
- **主持人 (A)**：{role_a}
- **专家 (B)**：{role_b}

**要求：**
1.  **格式**：只返回一个JSON对象数组。每个对象必须包含 "speaker" ("A" 或 "B") 和 "text" (对话内容)。不要返回任何其他内容。
2.  **风格**：{tone}
3.  **语言**：{language_instruction}。
4.  **长度**：{length}
5.  **结构**：
{structure}

**输入文本：**
{text_snippet}

**输出格式示例：**
[
    {{"speaker": "A", "text": "..."}},
    {{"speaker": "B", "text": "..."}},
    {{"speaker": "A", "text": "..."}}
]
"""

        # 截取文本以适应上下文窗口
        text_snippet = text[:30000]

        if language == "zh":
            lang_instruction = "对话必须使用中文（普通话）。即使输入文本是英文或其他语言，你也必须将内容翻译并改编为自然的中文口语。"
        else:
            lang_instruction = "The dialogue MUST be in English. Even if the input text is in Chinese, you MUST translate and adapt the content into natural, spoken English."

        formatted_prompt = system_prompt.format(
            role_a=cfg["role_a"],
            role_b=cfg["role_b"],
            tone=cfg["tone"],
            length=cfg["length"],
            structure=cfg["structure"],
            language_instruction=lang_instruction,
            text_snippet=text_snippet,
        )
        
        try:
            response = await self.lc_client.ainvoke([
                SystemMessage(content="You are a helpful assistant capable of generating JSON output."),
                HumanMessage(content=formatted_prompt)
            ])
            
            content = response.content
            # 清理 Markdown 代码块 (```json ... ```)
            if "```" in content:
                content = content.replace("```json", "").replace("```", "")
            
            script = json.loads(content)
            return script
        except Exception as e:
            logger.error(f"LLM Script Generation Error: {e}")
            raise

    async def _generate_audio_segments(self, script: List[Dict], temp_dir: Path) -> List[str]:
        """
        并发生成音频片段
        """
        tasks = []
        file_paths = []
        
        # 音色配置 (CosyVoice / Sambert)
        # 假设使用 DashScope 提供的预设音色
        # Host A: 龙悦 (longyue) - 知性女声
        # Expert B: 龙婉 (longwan) - 柔和女声 (或者找个男声 if available)
        # 查看 dashscope 文档，常用音色：
        # sambert-zhichu-v1 (男), sambert-zhiwei-v1 (女) ... 
        # CosyVoice: cosyvoice-v1, voice names: "longyue", "longwan" etc.
        # 为了保险起见，使用通用的 Sambert 音色或者 CosyVoice Public 音色
        
        VOICE_MAP = {
            "A": settings.podcast_voice_host or "sambert-zhiwei-v1",      # Host 默认女声
            "B": settings.podcast_voice_expert or "sambert-zhichu-v1"   # Expert 默认男声
        }
        
        # 如果是英文，使用兼容音色
        
        for i, line in enumerate(script):
            speaker = line.get("speaker", "A")
            text = line.get("text", "")
            if not text:
                continue
                
            voice = VOICE_MAP.get(speaker, "sambert-zhichu-v1")
            file_path = temp_dir / f"{i:04d}_{speaker}.mp3"
            file_paths.append(str(file_path))

            if speaker == "B":
                fallback_voices = [voice, "sambert-zhichu-v1", "sambert-zhitian-v1", "sambert-zhiwei-v1"]
            else:
                fallback_voices = [voice, "sambert-zhiwei-v1", "sambert-zhichu-v1"]

            dedup_voices = []
            for voice_name in fallback_voices:
                if voice_name and voice_name not in dedup_voices:
                    dedup_voices.append(voice_name)

            tasks.append(self._tts_single_line(text, dedup_voices, str(file_path)))
            
        # 并发执行 (限制并发数以免触发限流)
        # DashScope QPS Limit might apply. Let's use a semaphore.
        semaphore = asyncio.Semaphore(5) 
        
        async def sem_task(task):
            async with semaphore:
                return await task
                
        await asyncio.gather(*[sem_task(t) for t in tasks])
        
        return file_paths

    async def _tts_single_line(self, text: str, voices: List[str], output_path: str):
        from dashscope.audio.tts import SpeechSynthesizer
        from pydub import AudioSegment
        """
        调用 DashScope TTS API
        """
        try:
            # 使用同步 SDK 的 call 方法，但在 executor 中运行以避免阻塞
            # 或者使用 dashscope 提供的异步流式接口 (比较复杂)，这里用 executor 简单
            
            def run_tts():
                last_error = "unknown"
                for voice in voices:
                    try:
                        result = SpeechSynthesizer.call(
                            api_key=self.tts_api_key,
                            model=voice,
                            text=text,
                            sample_rate=48000,
                            format='mp3'
                        )
                        audio_data = result.get_audio_data()
                        if audio_data is not None:
                            with open(output_path, 'wb') as f:
                                f.write(audio_data)
                            return True, voice, None

                        last_error = getattr(result, 'message', None) or 'no audio data returned'
                        logger.warning(f"TTS voice '{voice}' returned empty audio: {last_error}")
                    except Exception as tts_err:
                        last_error = f"{type(tts_err).__name__}: {tts_err}"
                        logger.warning(f"TTS voice '{voice}' failed: {last_error}")

                return False, None, last_error
                    
            loop = asyncio.get_running_loop()
            ok, used_voice, error_msg = await loop.run_in_executor(None, run_tts)
            if ok:
                logger.debug(f"TTS succeeded with voice '{used_voice}'")
                return

            logger.error(f"TTS all voices failed for line '{text[:20]}...': {error_msg}")
            AudioSegment.silent(duration=1000).export(output_path, format="mp3")
            
        except Exception as e:
            logger.error(f"TTS Error for line '{text[:20]}...': {e}")
            # 生成静音作为替补，防止文件缺失导致合并失败
            AudioSegment.silent(duration=1000).export(output_path, format="mp3")

    def _merge_audio(self, file_paths: List[str], output_path: str):
        from pydub import AudioSegment
        """
        合并音频文件
        """
        combined = AudioSegment.empty()
        
        for fp in file_paths:
            if os.path.exists(fp):
                try:
                    seg = AudioSegment.from_mp3(fp)
                    combined += seg
                    # 添加短暂间隔 (300ms) 让对话更自然
                    combined += AudioSegment.silent(duration=300) 
                except Exception as e:
                    logger.error(f"Error merging segment {fp}: {e}")
        
        combined.export(output_path, format="mp3")

    # Database Helpers
    async def _update_status(self, task_id: int, status: str, progress: int, stage: str, result_path: str = None, error: str = None):
        if not self.db:
            return
        
        try:
            # 使用参数绑定（不再拼接 SQL 字符串，避免注入与转义问题）
            values = {"status": status, "progress": progress, "current_stage": stage}
            if result_path:
                values["audio_file_path"] = result_path
            if error:
                values["error_message"] = error

            from sqlalchemy import update
            await self.db.execute(
                update(DocumentPodcast).where(DocumentPodcast.id == task_id).values(**values)
            )
            await self.db.commit()
        except Exception as e:
            logger.error(f"DB Update Error: {e}")

    async def _update_script(self, task_id: int, script: List[Dict]):
        if not self.db:
            return
            
        try:
            # 使用参数绑定；script 是 JSON 列，交给 SQLAlchemy 序列化
            from sqlalchemy import update
            await self.db.execute(
                update(DocumentPodcast).where(DocumentPodcast.id == task_id).values(script=script)
            )
            await self.db.commit()
        except Exception as e:
            logger.error(f"DB Update Script Error: {e}")

