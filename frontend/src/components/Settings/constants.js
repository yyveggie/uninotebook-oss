/** 对话模型（任一即可）；能力清单全缺时合并成一句「对话模型」，而不是罗列三家 */
export const CHAT_PROVIDERS = ['qwen', 'moonshot', 'deepseek', 'openai']

/** 可选服务：不配置不影响主要功能 */
export const OPTIONAL_PROVIDERS = ['image_gen', 'dashscope_native']
