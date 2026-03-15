# OpenWeather API 연동 — 전력 예측용 날씨

## 개요

- **OpenWeather API**로 현재 날씨를 조회해 전력 사용량·PV 예측에 반영합니다.
- **LLM 도구**: 예측 계획 수립 시 ForecastPlanner가 현재 날씨를 프롬프트에 포함합니다.
- **Feature**: 시계열에 `weather_temp_c`, `weather_humidity_pct`, `weather_clouds_pct`, `weather_wind_speed_ms` 4개 컬럼이 추가됩니다.

## .env 설정

프로젝트 루트 `.env`에 다음을 추가하세요.

```env
OPENWEATHER_API_KEY=발급받은_API_키
OPENWEATHER_CITY=Seoul
OPENWEATHER_LAT=37.5665
OPENWEATHER_LON=126.9780
OPENWEATHER_UNITS=metric
```

- API 키 발급: https://openweathermap.org/api
- `OPENWEATHER_API_KEY`가 비어 있으면 **서울 월별 평균값**으로 대체됩니다 (학습·테스트 가능).

## 사용처

1. **FeatureEngineeringAgent**  
   - `get_weather_for_dataframe()`로 학습/검증 데이터에 날씨 컬럼 추가.  
   - API 키가 있으면 “오늘” 구간에 한해 현재 날씨를 반영.

2. **ForecastPlannerAgent**  
   - `get_current_weather_tool("Seoul")`로 현재 날씨 문자열을 가져와 LLM 프롬프트의 `[현재 날씨]` 블록에 넣습니다.

3. **LLM 도구로 등록**  
   - `alfp.tools.get_llm_tools()` → LangChain `bind_tools`에 넘겨 에이전트에서 날씨 조회 도구로 사용할 수 있습니다.
   - `create_weather_tool_for_llm()` → 단일 날씨 도구 인스턴스.

## 코드 예시

```python
from alfp.tools.openweather import get_current_weather, get_current_weather_tool, get_llm_tools

# 현재 날씨 dict
w = get_current_weather(city="Seoul")

# LLM용 문자열
text = get_current_weather_tool("Seoul")

# LangChain 도구 목록 (다른 에이전트에서 bind_tools용)
tools = get_llm_tools()
```
