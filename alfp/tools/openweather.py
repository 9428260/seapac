"""
OpenWeather API 연동 — 전력 예측용 날씨 정보
- LLM tool: 현재 날씨 조회 (예측 계획 시 참고)
- Feature: 시계열에 날씨 feature 추가 (기온, 습도, 운량, 풍속)
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

# .env 로드
try:
    from dotenv import load_dotenv
    _env = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(_env, override=False)
except Exception:
    pass

# 서울 월별 평균 (과거 데이터용 — API 없을 때 또는 학습용)
# 출처: 기상청 통계 보정값 (℃, %, 0–100, m/s)
SEOUL_MONTHLY_WEATHER = {
    1:  {"temp_c": -2.4, "humidity_pct": 59, "clouds_pct": 45, "wind_speed_ms": 2.8},
    2:  {"temp_c": 1.1,  "humidity_pct": 58, "clouds_pct": 48, "wind_speed_ms": 2.9},
    3:  {"temp_c": 6.1,  "humidity_pct": 58, "clouds_pct": 50, "wind_speed_ms": 3.0},
    4:  {"temp_c": 12.5, "humidity_pct": 60, "clouds_pct": 52, "wind_speed_ms": 2.9},
    5:  {"temp_c": 17.8, "humidity_pct": 65, "clouds_pct": 50, "wind_speed_ms": 2.5},
    6:  {"temp_c": 22.2, "humidity_pct": 72, "clouds_pct": 55, "wind_speed_ms": 2.3},
    7:  {"temp_c": 25.4, "humidity_pct": 78, "clouds_pct": 58, "wind_speed_ms": 2.2},
    8:  {"temp_c": 26.2, "humidity_pct": 75, "clouds_pct": 52, "wind_speed_ms": 2.2},
    9:  {"temp_c": 21.8, "humidity_pct": 70, "clouds_pct": 48, "wind_speed_ms": 2.3},
    10: {"temp_c": 15.0, "humidity_pct": 64, "clouds_pct": 42, "wind_speed_ms": 2.5},
    11: {"temp_c": 7.4,  "humidity_pct": 61, "clouds_pct": 45, "wind_speed_ms": 2.7},
    12: {"temp_c": 0.4,  "humidity_pct": 61, "clouds_pct": 46, "wind_speed_ms": 2.8},
}

BASE_URL = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"


def get_current_weather(
    api_key: Optional[str] = None,
    city: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    units: str = "metric",
) -> dict[str, Any]:
    """
    OpenWeather API로 현재 날씨를 조회합니다.

    Args:
        api_key: API 키 (None이면 OPENWEATHER_API_KEY 환경변수 사용)
        city: 도시명 (예: "Seoul")
        lat, lon: 위·경도 (city 미지정 시 사용)
        units: metric(℃) | imperial(℉)

    Returns:
        {
            "temp_c", "humidity_pct", "clouds_pct", "wind_speed_ms",
            "description", "fetched_at", "source"
        }
        API 실패 시 source="monthly_avg" 로 월별 평균 반환.
    """
    api_key = api_key or os.environ.get("OPENWEATHER_API_KEY")
    city = city or os.environ.get("OPENWEATHER_CITY", "Seoul")
    lat = lat if lat is not None else _float_env("OPENWEATHER_LAT", 37.5665)
    lon = lon if lon is not None else _float_env("OPENWEATHER_LON", 126.978)
    units = units or os.environ.get("OPENWEATHER_UNITS", "metric")

    if not api_key:
        month = datetime.now().month
        out = dict(SEOUL_MONTHLY_WEATHER[month])
        out["description"] = "monthly_avg (API key not set)"
        out["fetched_at"] = datetime.now().isoformat()
        out["source"] = "monthly_avg"
        return out

    try:
        import urllib.request
        import json

        params = {"appid": api_key, "units": units}
        if city:
            params["q"] = city
        else:
            params["lat"] = lat
            params["lon"] = lon
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{BASE_URL}?{qs}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        month = datetime.now().month
        out = dict(SEOUL_MONTHLY_WEATHER[month])
        out["description"] = "monthly_avg (API error)"
        out["fetched_at"] = datetime.now().isoformat()
        out["source"] = "monthly_avg"
        return out

    main = data.get("main", {})
    wind = data.get("wind", {})
    weather = (data.get("weather") or [{}])[0]
    clouds = data.get("clouds", {})

    return {
        "temp_c": float(main.get("temp", 15)),
        "humidity_pct": int(main.get("humidity", 60)),
        "clouds_pct": int(clouds.get("all", 50)),
        "wind_speed_ms": float(wind.get("speed", 2.5)),
        "description": str(weather.get("description", "")),
        "fetched_at": datetime.now().isoformat(),
        "source": "openweather_api",
    }


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def get_weather_for_dataframe(
    df: pd.DataFrame,
    api_key: Optional[str] = None,
    current_weather: Optional[dict] = None,
    timestamp_column: str = "timestamp",
) -> pd.DataFrame:
    """
    시계열 DataFrame에 날씨 feature를 추가합니다.

    - current_weather가 있고 해당 날짜가 "오늘"이면 API/전달값 사용.
    - 그 외는 서울 월별 평균으로 채웁니다.

    Returns:
        df에 다음 컬럼이 추가된 복사본:
        weather_temp_c, weather_humidity_pct, weather_clouds_pct, weather_wind_speed_ms
    """
    df = df.copy()
    ts = pd.to_datetime(df[timestamp_column])
    if ts.dt.tz is not None:
        ts = ts.dt.tz_localize(None)

    for col in ["weather_temp_c", "weather_humidity_pct", "weather_clouds_pct", "weather_wind_speed_ms"]:
        if col not in df.columns:
            df[col] = np.nan

    month = ts.dt.month
    for key, default_d in SEOUL_MONTHLY_WEATHER.items():
        mask = month == key
        if not mask.any():
            continue
        df.loc[mask, "weather_temp_c"] = default_d["temp_c"]
        df.loc[mask, "weather_humidity_pct"] = default_d["humidity_pct"]
        df.loc[mask, "weather_clouds_pct"] = default_d["clouds_pct"]
        df.loc[mask, "weather_wind_speed_ms"] = default_d["wind_speed_ms"]

    # 오늘 날짜에 대해 current_weather 반영
    if current_weather:
        now = pd.Timestamp.now()
        if now.tzinfo is not None:
            now = now.tz_localize(None)
        today = now.normalize()
        today_mask = ts.dt.normalize() == today
        if today_mask.any():
            df.loc[today_mask, "weather_temp_c"] = current_weather.get("temp_c", df.loc[today_mask, "weather_temp_c"].iloc[0])
            df.loc[today_mask, "weather_humidity_pct"] = current_weather.get("humidity_pct", df.loc[today_mask, "weather_humidity_pct"].iloc[0])
            df.loc[today_mask, "weather_clouds_pct"] = current_weather.get("clouds_pct", df.loc[today_mask, "weather_clouds_pct"].iloc[0])
            df.loc[today_mask, "weather_wind_speed_ms"] = current_weather.get("wind_speed_ms", df.loc[today_mask, "weather_wind_speed_ms"].iloc[0])

    return df


def get_weather_forecast_for_dataframe(
    df: pd.DataFrame,
    horizon_steps: int = 4,
    timestamp_column: str = "timestamp",
) -> pd.DataFrame:
    """
    단기 운영 모드용 날씨 예보형 feature를 추가합니다.

    현재 구현은 외부 예보 API가 없을 때도 동작하도록 월별 평균 기반 forecast proxy를 사용합니다.
    향후 실제 forecast API 연동 시 이 함수를 교체하면 됩니다.
    """
    df = get_weather_for_dataframe(df, timestamp_column=timestamp_column)
    out = df.copy()
    ts = pd.to_datetime(out[timestamp_column])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_localize(None)

    forecast = get_weather_forecast(horizon_steps=horizon_steps)
    out["weather_forecast_temp_c"] = forecast["temp_c"]
    out["weather_forecast_humidity_pct"] = forecast["humidity_pct"]
    out["weather_forecast_clouds_pct"] = forecast["clouds_pct"]
    out["weather_forecast_wind_speed_ms"] = forecast["wind_speed_ms"]
    out["weather_forecast_horizon_steps"] = int(horizon_steps)
    return out


def get_weather_forecast(
    api_key: Optional[str] = None,
    city: Optional[str] = None,
    horizon_steps: int = 4,
) -> dict[str, Any]:
    """
    OpenWeather forecast API(3시간 간격)를 사용해 단기 예보를 조회한다.
    15분 단위 step은 가장 가까운 forecast slot으로 근사한다.
    """
    api_key = api_key or os.environ.get("OPENWEATHER_API_KEY")
    city = city or os.environ.get("OPENWEATHER_CITY", "Seoul")
    horizon_hours = max(1, horizon_steps) * 0.25

    if not api_key:
        month = datetime.now().month
        out = dict(SEOUL_MONTHLY_WEATHER[month])
        out["source"] = "monthly_avg_forecast"
        out["horizon_hours"] = horizon_hours
        return out

    try:
        import urllib.request
        import json

        url = f"{FORECAST_URL}?appid={api_key}&units=metric&q={city}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("list") or []
        if not items:
            raise ValueError("forecast list empty")
        target = datetime.utcnow().timestamp() + (horizon_hours * 3600)
        best = min(
            items,
            key=lambda item: abs(float(item.get("dt", 0)) - target),
        )
        main = best.get("main", {})
        wind = best.get("wind", {})
        clouds = best.get("clouds", {})
        return {
            "temp_c": float(main.get("temp", 15)),
            "humidity_pct": int(main.get("humidity", 60)),
            "clouds_pct": int(clouds.get("all", 50)),
            "wind_speed_ms": float(wind.get("speed", 2.5)),
            "source": "openweather_forecast_api",
            "horizon_hours": horizon_hours,
        }
    except Exception:
        month = datetime.now().month
        out = dict(SEOUL_MONTHLY_WEATHER[month])
        out["source"] = "monthly_avg_forecast"
        out["horizon_hours"] = horizon_hours
        return out


def get_current_weather_tool(city: str = "Seoul") -> str:
    """
    LLM이 호출할 수 있는 도구 — 현재 날씨를 문자열로 반환합니다.
    LangChain @tool 또는 Agent에 바인딩할 때 사용합니다.
    """
    w = get_current_weather(city=city or None)
    return (
        f"현재 날씨 ({w.get('fetched_at', '')[:19]}): "
        f"기온 {w['temp_c']:.1f}℃, 습도 {w['humidity_pct']}%, "
        f"운량 {w['clouds_pct']}%, 풍속 {w['wind_speed_ms']:.1f}m/s. "
        f"설명: {w.get('description', '')}. (출처: {w.get('source', 'api')})"
    )


# LangChain Tool 인스턴스 (선택 사용)
def create_weather_tool_for_llm():
    """LangChain StructuredTool 또는 @tool 데코레이터로 사용할 수 있는 도구 생성."""
    try:
        from langchain_core.tools import tool

        @tool
        def get_current_weather_llm(city: str = "Seoul") -> str:
            """현재 지정 도시의 날씨를 조회합니다. 전력 수요 예측 시 기온·습도·운량을 참고할 수 있습니다."""
            return get_current_weather_tool(city=city)

        return get_current_weather_llm
    except ImportError:
        return None
