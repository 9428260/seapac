"""
LLM 팩토리 - Azure OpenAI (GPT-4o) 클라이언트 공유 인스턴스
프로젝트 루트의 .env 파일을 자동으로 로드합니다.
LLM 연계 시 입출력은 logs/llm_io_YYYYMMDD.log 에 기록됩니다.

통합 제어:
- `SEAPAC_LLM_MODE`: `off | forecast | forecast_plan | core | market | plan | all`
- `ALFP_DISABLE_LLM`: 하위 호환용. 설정 시 `off`로 취급
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Union

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI

from alfp.llm_logging import get_llm_io_handler

_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path, override=False)

_VALID_LLM_MODES = ("off", "forecast", "forecast_plan", "core", "market", "plan", "all")
_MODE_RANK = {mode: idx for idx, mode in enumerate(_VALID_LLM_MODES)}
_STAGE_MIN_MODE = {
    "alfp_forecast_planner": "forecast",
    "alfp_validation": "core",
    "alfp_decision": "core",
    "seapac_self_critic": "market",
    "cda_strategy": "market",
    "agentscope_policy": "market",
    "agentscope_coordinator": "market",
    "parallel_policy": "market",
    "parallel_eco": "market",
    "parallel_storage": "market",
    "execution_summary": "market",
    "execution_merge": "market",
    "evaluation_summary": "core",
    "agent_plan": "plan",
    "governance_critic": "all",
    "default": "all",
}


def _legacy_disable_flag() -> bool:
    v = os.environ.get("ALFP_DISABLE_LLM", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def get_llm_mode() -> str:
    """현재 LLM 모드를 반환."""
    if _legacy_disable_flag():
        return "off"
    mode = os.environ.get("SEAPAC_LLM_MODE", "all").strip().lower()
    return mode if mode in _VALID_LLM_MODES else "all"


def set_llm_mode(mode: str) -> None:
    """프로세스 단위 LLM 모드 설정."""
    normalized = (mode or "").strip().lower()
    if normalized not in _VALID_LLM_MODES:
        raise ValueError(f"지원하지 않는 LLM mode: {mode}")
    os.environ["SEAPAC_LLM_MODE"] = normalized
    if normalized == "off":
        os.environ["ALFP_DISABLE_LLM"] = "1"
    else:
        os.environ.pop("ALFP_DISABLE_LLM", None)
    get_llm.cache_clear()
    get_llm_forced.cache_clear()


def is_llm_enabled(stage: str = "default") -> bool:
    """현재 stage가 LLM 호출 가능한지 여부."""
    mode = get_llm_mode()
    if mode == "off":
        return False
    if mode == "forecast_plan":
        return stage in ("alfp_forecast_planner", "agent_plan")
    required = _STAGE_MIN_MODE.get(stage, _STAGE_MIN_MODE["default"])
    return _MODE_RANK[mode] >= _MODE_RANK[required]


def is_llm_disabled(stage: str = "default") -> bool:
    """stage 기준 LLM 비활성 여부."""
    return not is_llm_enabled(stage)


class _StubLLM:
    """LLM 비활성 시 사용하는 스텁."""

    def invoke(self, *args, **kwargs):
        raise RuntimeError(
            "LLM 연계가 비활성화되었습니다 (SEAPAC_LLM_MODE / ALFP_DISABLE_LLM). "
            "규칙 기반 폴백을 사용하려면 호출부의 except에서 처리됩니다."
        )


_stub_llm = _StubLLM()


def _build_client(temperature: float) -> AzureChatOpenAI:
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


@lru_cache(maxsize=16)
def get_llm(temperature: float = 0.0, stage: str = "default") -> Union[AzureChatOpenAI, _StubLLM]:
    """
    통합 LLM mode를 따르는 LLM 인스턴스를 반환.
    """
    if is_llm_disabled(stage):
        return _stub_llm
    return _build_client(temperature)


@lru_cache(maxsize=16)
def get_llm_forced(temperature: float = 0.0, stage: str = "default") -> Union[AzureChatOpenAI, _StubLLM]:
    """
    기존 forced 호출부 호환용.
    현재는 통합 LLM mode를 따르며, stage가 허용된 경우만 실제 LLM을 반환한다.
    """
    if is_llm_disabled(stage):
        return _stub_llm
    return _build_client(temperature)
