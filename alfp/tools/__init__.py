"""
ALFP 도구 모듈 - OpenWeather 등 외부 API 연동
LLM tool: get_current_weather_llm — 전력 예측 계획 시 날씨 조회용
"""

from alfp.tools.openweather import (
    get_current_weather,
    get_weather_for_dataframe,
    get_current_weather_tool,
    create_weather_tool_for_llm,
)

__all__ = [
    "get_current_weather",
    "get_weather_for_dataframe",
    "get_current_weather_tool",
    "create_weather_tool_for_llm",
    "get_llm_tools",
]


def get_llm_tools():
    """LLM에 바인딩할 수 있는 도구 목록 (LangChain bind_tools용)."""
    tools = []
    weather_tool = create_weather_tool_for_llm()
    if weather_tool is not None:
        tools.append(weather_tool)
    return tools
