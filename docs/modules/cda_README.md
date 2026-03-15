# CDA (Continuous Double Auction) Energy Market

[prd/cda_energy_market_prd.md](../prd/cda_energy_market_prd.md)에 따른 CDA 기반 전력 시장 구현.  
**seapac_agents**의 **MarketCoordinator**와 **Execution**을 대체하여 사용할 수 있습니다.

## 구조

| 모듈 | 역할 (PRD) |
|------|------------|
| `orderbook.py` | Bid / Ask 테이블 (§7) |
| `matching.py` | CDA 매칭 엔진 — Bid 내림차순, Ask 오름차순, Highest Bid ≥ Lowest Ask, 거래가 = (Bid+Ask)/2 (§8) |
| `buyer.py` | Buyer Agent — Deficit/Market Price/Peak Risk 기반 구매 입찰 생성 (§6) |
| `coordinator.py` | Market Coordinator — Order Book 관리, 매칭 실행, 거래 체결, 시장 통계 (§6). SmartSeller/StorageMaster/EcoSaver 제안 + Policy 검증 후 동일한 `decisions` 형식 반환 |
| `settlement.py` | Settlement Engine — 거래 기록 반영, 정책 검증, Mesa 업데이트 (§9). `run_execution(decisions, ...)` → `ExecutionResult` (기존 Execution과 동일 인터페이스) |
| `strategy_agent.py` | Strategy Agent (LLM) — [cda_strategy_negotiation_prd.md](../prd/cda_strategy_negotiation_prd.md) §3. Bid/Ask·ESS·DR 전략 권고 + 설명 가능 reasoning log |
| `negotiation.py` | Negotiation Layer — PRD §4. 전략 제안 + 에이전트 제안 공유 → 충돌 해결 → 합의안 → CDA 제출, 협상 감사 로그 (FR-8) |

## 사용 방법

### 파이프라인에서 CDA 사용

```bash
PYTHONPATH=. python seapac_agents/run_agentic_pipeline.py --use-cda
PYTHONPATH=. python seapac_agents/run_agentic_pipeline.py --use-cda --steps 96 --save-json --output-dir output
```

- `--use-cda` 미지정: 기존 MarketCoordinator + seapac_agents.execution 사용  
- `--use-cda` 지정: CDA 코디네이터 + cda.settlement 사용 (Step 3·4만 교체, Step 2/5 동일)

### Python에서 직접 사용

```python
from cda import (
    OrderBook,
    match_cda,
    run_cda_step,
    run_cda_decision_series_with_agents,
    run_execution,
    ExecutionResult,
)
from seapac_agents.decision import (
    _init_agentscope,
    PolicyAgentAS,
    SmartSellerAgentAS,
    StorageMasterAgentAS,
    EcoSaverAgentAS,
    _PROMPTS,
)

# CDA 의사결정 (에이전트 + 매칭)
_init_agentscope()
policy = PolicyAgentAS(max_charge_kw=50, max_discharge_kw=50)
seller = SmartSellerAgentAS()
storage = StorageMasterAgentAS()
eco_saver = EcoSaverAgentAS(peak_threshold_kw=500)
decisions = run_cda_decision_series_with_agents(
    state_json_list,
    policy, seller, storage, eco_saver,
    state_message_template=_PROMPTS["state_message_template"],
)

# Settlement → Mesa 실행
result = run_execution(decisions, data_path="...", n_steps=96, phase=4, ...)
```

## Mesa 연동

CDA 코디네이터가 반환하는 `decisions`는 기존 형식과 동일합니다.

- `ess_schedule`: ESS 스케줄 (StorageMaster + Policy 검증)
- `trading_recommendations`: CDA 매칭 결과 — 각 체결건당 `timestamp`, `surplus_kw`, `bid_price`(거래가), `action: "sell_p2p"`
- `demand_response_events`: EcoSaver + Policy 검증

따라서 `simulation.model.ALFPSimulationModel(alfp_decisions=decisions)` 및 `seapac_agents.execution.run_execution`과 그대로 호환됩니다. CDA Settlement는 내부적으로 동일한 `run_execution`을 호출합니다.

---

## Strategy Agent + Negotiation (cda_strategy_negotiation_prd.md)

**흐름**: Forecast/State → **Strategy Agent (LLM)** → **Negotiation Layer** → Policy/Trust → CDA Market → Settlement.

### Strategy Agent (§3)

- 입력: Energy forecast, Market price, ESS 상태, Peak risk (state_json).
- 출력: Bid/Ask·ESS·DR 권고 + **reasoning log** (설명 가능 전략).
- `cda.generate_strategy(state_json, use_llm=True)` → `StrategyRecommendation`.
- LLM 미사용 또는 장애 시 규칙 기반 폴백.

### Negotiation Layer (§4)

- 참여: Strategy Agent, SmartSeller, StorageMaster, EcoSaver, Policy.
- 단계: (1) 전략 제안 (2) 에이전트 제안 공유 (3) 충돌 해결 (4) 합의안 (5) CDA 제출.
- `cda.run_negotiation(state_json, strategy_rec, seller_msg, storage_msg, eco_msg, policy_agent)` → `NegotiationResult` (합의 제안 + `negotiation_log`).

### 파이프라인에서 Strategy + Negotiation 사용

```python
from cda import (
    run_cda_decision_series_with_agents_and_negotiation,
    run_execution,
)
from seapac_agents.decision import (
    _init_agentscope,
    PolicyAgentAS,
    SmartSellerAgentAS,
    StorageMasterAgentAS,
    EcoSaverAgentAS,
    _PROMPTS,
)

_init_agentscope()
policy = PolicyAgentAS(max_charge_kw=50, max_discharge_kw=50)
seller = SmartSellerAgentAS()
storage = StorageMasterAgentAS()
eco_saver = EcoSaverAgentAS(peak_threshold_kw=500)
decisions = run_cda_decision_series_with_agents_and_negotiation(
    state_json_list,
    policy, seller, storage, eco_saver,
    state_message_template=_PROMPTS["state_message_template"],
    use_llm_strategy=True,
)
# decisions에 strategy_reasoning_logs, negotiation_logs 포함
result = run_execution(decisions, data_path="...", n_steps=96, phase=4, ...)
```
