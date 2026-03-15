"""
LLM 팩토리 - Azure OpenAI (GPT-4o) 클라이언트 공유 인스턴스
프로젝트 루트의 .env 파일을 자동으로 로드합니다.
LLM 연계 시 입출력은 logs/llm_io_YYYYMMDD.log 에 기록됩니다.

일시 비활성화: 환경변수 ALFP_DISABLE_LLM=1 (또는 true/yes) 로 설정하면
LLM 호출 없이 규칙 기반 폴백만 사용합니다.
"""

import os
from pathlib import Path
from functools import lru_cache
from typing import Union

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI

from alfp.llm_logging import get_llm_io_handler

# 프로젝트 루트의 .env 로드 (이미 로드된 경우 덮어쓰지 않음)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path, override=False)


def is_llm_disabled() -> bool:
    """환경변수로 LLM 연계가 비활성화되었는지 여부."""
    v = os.environ.get("ALFP_DISABLE_LLM", "").strip().lower()
    return v in ("1", "true", "yes", "on")


class _StubLLM:
    """LLM 비활성화 시 사용하는 스텁. invoke() 호출 시 예외를 발생시켜 규칙 기반 폴백으로 유도."""

    def invoke(self, *args, **kwargs):
        raise RuntimeError(
            "LLM 연계가 비활성화되었습니다 (ALFP_DISABLE_LLM). "
            "규칙 기반 폴백을 사용하려면 호출부의 except에서 처리됩니다. "
            "LLM을 다시 사용하려면 ALFP_DISABLE_LLM을 제거하거나 0으로 설정한 뒤 프로세스를 재시작하세요."
        )


_stub_llm = _StubLLM()


@lru_cache(maxsize=1)
def get_llm(temperature: float = 0.0) -> Union[AzureChatOpenAI, _StubLLM]:
    """
    Azure OpenAI LLM 인스턴스를 반환합니다 (싱글톤).

    환경변수:
        AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY
        AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION
        ALFP_DISABLE_LLM: 1 / true / yes 이면 LLM 호출 비활성화 (규칙 기반만 사용)
    """
    if is_llm_disabled():
        return _stub_llm

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
        callbacks=[get_llm_io_handler()],
    )


def get_llm_forced(temperature: float = 0.0) -> AzureChatOpenAI:
    """
    ALFP_DISABLE_LLM 설정을 무시하고 항상 실제 LLM 인스턴스를 반환합니다.

    AgentPlan 등 LLM 연계가 필수인 컴포넌트에서 사용합니다.
    ALFP_DISABLE_LLM=1 이어도 LLM을 호출합니다.

    환경변수:
        AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY (필수)
        AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION
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
        callbacks=[get_llm_io_handler()],
    )
