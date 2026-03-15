# 영구 메모리 (Persistent Memory)

런 간·세션 간에 이전 실행 결과를 기억하기 위한 저장소입니다.

## 동작

- **저장**: 파이프라인 종료 시(DecisionAgent 이후) `save_memory_node`가 현재 런의 요약(계획, 검증 지표, 의사결정 요약)을 프로슈머 ID별로 저장합니다.
- **로드**: 파이프라인 시작 시(DataLoader) `load_memory(prosumer_id)`로 이전 런 요약을 읽어 `state["persistent_memory"]`에 넣습니다.
- **저장 위치**: 프로젝트 루트의 `memory_store/<prosumer_id>.json` (프로슈머 ID는 파일명에 맞게 이스케이프됨).

## 활용

- **ForecastPlannerAgent**: 재계획 시 `persistent_memory["last_plan"]`, `state["validation_metrics"]`를 참고해 다른 모델·설정을 제안합니다.
- **Fallback**: LLM 실패 시 이전 모델이 있으면 반대 모델(lgbm ↔ xgboost)을 선택합니다.

## 데이터 형식

저장되는 JSON 예시:

```json
{
  "last_run_at": "2026-03-15T12:00:00Z",
  "last_plan": { "selected_model": "lgbm", "forecast_horizon_steps": 96, "llm_reasoning": "..." },
  "last_validation_metrics": { "kpi": {...}, "load": {...}, ... },
  "last_decisions_summary": { "ess_summary": {...}, "tariff_saving": {...}, ... }
}
```
