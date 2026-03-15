"""
LLM 팩토리 - Azure OpenAI (GPT-4o) 클라이언트 공유 인스턴스
프로젝트 루트의 .env 파일을 자동으로 로드합니다.
"""

import os
from pathlib import Path
from functools import lru_cache

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI

# 프로젝트 루트의 .env 로드 (이미 로드된 경우 덮어쓰지 않음)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path, override=False)


@lru_cache(maxsize=1)
def get_llm(temperature: float = 0.0) -> AzureChatOpenAI:
    """
    Azure OpenAI LLM 인스턴스를 반환합니다 (싱글톤).

    환경변수:
        AZURE_OPENAI_ENDPOINT
        AZURE_OPENAI_API_KEY
        AZURE_OPENAI_DEPLOYMENT
        AZURE_OPENAI_API_VERSION
    """
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")

    if not endpoint or not api_key:
        raise EnvironmentError(
            ".env 파일에 AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY가 설정되지 않았습니다."
        )

    return AzureChatOpenAI(
        azure_endpoint=endpoint,
        azure_deployment=deployment,
        api_key=api_key,
        api_version=api_version,
        temperature=temperature,
    )
