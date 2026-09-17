import os

from dotenv import find_dotenv, load_dotenv
from langchain.chat_models import init_chat_model


load_dotenv(find_dotenv())


# 主 Agent 模型
model = init_chat_model(
    model=os.getenv("LLM_QWEN_MAX"),
    model_provider="openai",
)


# 长期记忆提取模型
memory_extractor_model = init_chat_model(
    model=os.getenv(
        "MEMORY_EXTRACTOR_MODEL",
        "qwen3-32b",
    ),
    model_provider="openai",
    extra_body={
        "enable_thinking": False,
    },
)