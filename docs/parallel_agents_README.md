# Final Parallel Execution Layer (SEAPAC)

PRD: **docs/prd/seapac_parallel_agents_prd.md**

## 개요

에너지 시장 협상 단계(Step 3)에서 나온 **후보 액션**을, 실행 직전에 **3개 에이전트가 병렬로 평가**하고, **Execution Orchestrator**가 결과를 합쳐 최종 실행 가능 액션만 남깁니다.

- **Policy Management Agent** — 정책·규제·안전 검증 (거부권)
- **Eco Saver Agent** — 절전 권고 생성 (자문만)
- **Storage Management Agent** — PV/ESS 물리적 가능성 검증 (거부권)

## 디렉터리 구조

| 파일 | 역할 |
|------|------|
| `contracts.py` | 데이터 계약: `decisions_to_candidate_bundle`, `orchestrator_output_to_decisions` |
| `policy_agent.py` | Policy Management Agent — 규칙 검증, 승인/거절/수정 |
| `eco_saver_agent.py` | Eco Saver Agent — 절전 권고, 예상 절감액, 알림 페이로드 |
| `storage_agent.py` | Storage Management Agent — PV 운영·ESS 운영(SoC, 열화 추정) |
| `orchestrator.py` | Execution Orchestrator — 병렬 실행 및 결과 병합, 거부 규칙 적용 |
| `audit_log.py` | 감사 로그 (PRD §11) — 후보 액션, 정책/스토리지 평가, 최종 결과 append-only 기록 |

## 사용 방법

### 파이프라인에서 사용

```bash
PYTHONPATH=. python seapac_agents/run_agentic_pipeline.py --use-parallel --audit-log output/parallel_audit.jsonl
```

### Python API

```python
from parallel_agents import (
    run_parallel_evaluation_and_convert,
    PolicyConfig,
)

# Step 3 decisions + state_json_list → 병렬 평가 → Step 4용 decisions
decisions_after = run_parallel_evaluation_and_convert(
    decisions,
    state_json_list=state_json_list,
    policy_config=PolicyConfig(max_charge_kw=50, max_discharge_kw=50),
    peak_threshold_kw=500,
    use_async=True,
)
# decisions_after["ess_schedule"], ["trading_recommendations"], ["demand_response_events"] → run_execution() 입력
# decisions_after["parallel_layer"]["recommendations"] → 주민 알림용
```

## 실패 처리 (PRD §9)

- **Policy Agent 장애** → 안전 모드, 실행 차단 (모든 액션 거절)
- **Storage Agent 장애** → 기기 제어 비활성화 (ESS만 거절, 시장/DR은 통과)
- **Eco Saver 장애** → 권고 없이 진행

## KPI (PRD §10)

- Policy: 정책 위반 방지율, 규칙 처리 지연
- Eco Saver: 권고 수락률, 세대별 절감
- Storage: 피크 감소율, ESS 열화 비용
- 시스템: 거래 수익 증가, 결정 지연, 에너지 비용 절감
