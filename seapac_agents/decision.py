"""
Step 3 — Multi-Agent Decision Engine (AgentScope 기반)

PRD 명세에 따라 AgentScope 프레임워크를 사용하여 5개 에이전트를 구현합니다.
각 에이전트는 sys_prompt를 통해 페르소나(Role)를 주입받습니다.

Agents (PRD):
  SmartSeller-Agent      : 잉여 에너지 수익 극대화
  StorageMaster-Agent    : ESS 운영 최적화
  EcoSaver-Agent         : 에너지 소비 절감 (DR)
  MarketCoordinator-Agent: 협상 조율 및 충돌 해결
  Policy-Agent           : 제약 조건 강제

AgentScope 페르소나 주입 방식:
  - 각 에이전트는 AgentBase를 상속하며 __init__에서 self.sys_prompt를 설정
  - sys_prompt는 에이전트의 목표·역할·제약을 자연어로 기술한 Role 페르소나
  - 에이전트 간 통신은 Msg(name, content, role, metadata) 객체로 수행
  - MsgHub를 통해 State Translator 출력을 전체 에이전트에 브로드캐스트

Pipeline:
  State JSON (Step 2) → MsgHub broadcast → [Policy, Seller, Storage, EcoSaver] 제안
  → MarketCoordinator 조율 → decisions dict (Step 4 입력)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import agentscope
from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope.pipeline import MsgHub

from dotenv import load_dotenv

# .env 로드 (alfp/llm.py와 동일 경로)
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path, override=False)


# ─────────────────────────────────────────────────────────────────
# 프롬프트 메시지 로드 (prompt_messages.json — 소스 수정 없이 메시지 변경 가능)
# ─────────────────────────────────────────────────────────────────

def _load_prompt_messages() -> dict[str, str]:
    """seapac_agents/prompt_messages.json 을 로드. 없거나 오류 시 기본값 반환."""
    import json
    path = Path(__file__).resolve().parent / "prompt_messages.json"
    defaults = {
        "persona_policy": "당신은 에너지 커뮤니티의 Policy-Agent입니다.\n\n역할: 모든 에이전트 제안에 대해 물리적·운영적 제약 조건을 강제합니다.\n목표: 안전하고 규정에 맞는 에너지 운영을 보장합니다.\n\n검증 항목:\n- ESS 충전: power_kw ≤ max_charge_kw, SoC ≤ soc_max\n- ESS 방전: power_kw ≤ max_discharge_kw, SoC ≥ soc_min\n- 거래: bid_quantity ≥ min_trade_kw, bid_price > 0\n- DR: recommended_reduction_kw ≥ 0\n\n위반 시: 해당 제안을 무효화하거나 클램핑하여 반환합니다.",
        "persona_smart_seller": "당신은 에너지 커뮤니티의 SmartSeller-Agent입니다.\n\n역할: 잉여 태양광 에너지를 최대 수익으로 판매합니다.\n목표: bid_price와 bid_quantity를 결정하여 커뮤니티 내 P2P 거래 또는 계통 판매를 통해 수익을 극대화합니다.\n\n제약 조건:\n- 판매 가격은 커뮤니티 P2P 가격 범위 내에서 설정합니다.\n- 피크 위험(HIGH)이 높을수록 더 높은 가격을 요구합니다.\n- 잉여가 없으면 판매하지 않습니다(hold).\n\n출력 형식 (metadata):\n{\n  \"action\": \"sell_p2p\" | \"sell_grid\" | \"hold\",\n  \"bid_price\": float,       # 원/kWh\n  \"bid_quantity_kw\": float, # 판매 희망 수량 (kW)\n  \"surplus_kw\": float,\n  \"reason\": str\n}",
        "persona_storage_master": "당신은 에너지 커뮤니티의 StorageMaster-Agent입니다.\n\n역할: 배터리 에너지 저장 시스템(ESS/BESS)의 충방전을 최적화합니다.\n목표: TOU 요금제와 피크 부하를 고려하여 ESS 운영 비용을 최소화하고 절감 효과를 극대화합니다.\n\n의사결정 우선순위:\n  1. 피크 부하 억제 (peak_risk=HIGH → 방전)\n  2. 잉여 PV 흡수 (surplus > 1kW → 충전)\n  3. TOU 저요금 시간대 충전\n  4. TOU 고요금 시간대 방전\n\n출력 형식 (metadata):\n{\n  \"action\": \"charge\" | \"discharge\" | \"idle\",\n  \"power_kw\": float,   # 충방전 전력 (kW)\n  \"soc_pct\": float,    # 현재 SoC (%)\n  \"reason\": str\n}",
        "persona_eco_saver": "당신은 에너지 커뮤니티의 EcoSaver-Agent입니다.\n\n역할: 수요반응(DR, Demand Response)을 통해 에너지 소비를 절감합니다.\n목표: 피크 초과 부하를 감지하고 적절한 DR 권고를 생성하여 사용자의 에너지 소비를 줄입니다.\n\n동작 규칙:\n- 부하가 피크 임계값 초과: 초과분의 30%를 감소 권고\n- 피크 위험이 MEDIUM: 예방적 5% 절감 권고\n- 피크 위험이 LOW: DR 권고 없음\n\n출력 형식 (metadata):\n{\n  \"dr_events\": [\n    {\n      \"timestamp\": str,\n      \"net_load_kw\": float,\n      \"recommended_reduction_kw\": float,\n      \"action\": \"demand_response\",\n      \"reason\": str\n    }\n  ]\n}",
        "persona_market_coordinator": "당신은 에너지 커뮤니티의 MarketCoordinator-Agent입니다.\n\n역할: SmartSeller, StorageMaster, EcoSaver의 제안을 수렴하여 충돌을 해결하고 최종 운영 결정을 내립니다.\n목표: 커뮤니티 전체 효율을 극대화하면서 개별 에이전트 목표를 균형있게 반영합니다.\n\n충돌 해결 규칙:\n- 피크 위험 HIGH이면 ESS 방전이 P2P 판매보다 우선\n- ESS 충전 중 잉여가 있으면 잉여 일부를 판매 허용\n- DR과 ESS 방전은 병행 허용\n\n출력: 최종 decisions dict (ess_schedule, trading_recommendations, demand_response_events)",
        "state_message_template": "커뮤니티 에너지 상태 [{time}]: 부하={total_load}kW, 피크위험={peak_risk}",
    }
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k in defaults:
                if k not in data:
                    continue
                v = data[k]
                if isinstance(v, list):
                    defaults[k] = "\n".join(str(line) for line in v)
                elif isinstance(v, str):
                    defaults[k] = v
    except Exception:
        pass
    return defaults


_PROMPTS = _load_prompt_messages()


def _safe_float(value, default: float = 0.0) -> float:
    """None/빈값/비수치 입력에도 안전하게 float 변환."""
    try:
        if value is None:
            return float(default)
        if isinstance(value, str) and not value.strip():
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


# ─────────────────────────────────────────────────────────────────
# Policy-Agent
# ─────────────────────────────────────────────────────────────────

class PolicyAgentAS(AgentBase):
    """
    제약 조건 강제 에이전트 (AgentScope 기반).

    sys_prompt를 통해 정책 집행자(Policy Enforcer) 페르소나가 주입됩니다.
    rule-based 검증을 수행하며 LLM을 사용하지 않습니다.
    """

    def __init__(
        self,
        max_charge_kw: float = 50.0,
        max_discharge_kw: float = 50.0,
        ess_soc_min_pct: float = 10.0,
        ess_soc_max_pct: float = 95.0,
        min_trade_kw: float = 0.2,
        dr_reduction_factor: float = 0.30,
    ):
        super().__init__()
        self.name = "Policy-Agent"
        self.sys_prompt = _PROMPTS["persona_policy"]
        self.max_charge_kw = max_charge_kw
        self.max_discharge_kw = max_discharge_kw
        self.ess_soc_min_pct = ess_soc_min_pct
        self.ess_soc_max_pct = ess_soc_max_pct
        self.min_trade_kw = min_trade_kw
        self.dr_reduction_factor = dr_reduction_factor
        self.violations: list[str] = []

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        pass

    async def reply(self, msg: Msg) -> Msg:
        """제약 조건 설정을 Msg로 반환."""
        constraints = {
            "max_charge_kw": self.max_charge_kw,
            "max_discharge_kw": self.max_discharge_kw,
            "ess_soc_min_pct": self.ess_soc_min_pct,
            "ess_soc_max_pct": self.ess_soc_max_pct,
            "min_trade_kw": self.min_trade_kw,
            "dr_reduction_factor": self.dr_reduction_factor,
        }
        return Msg(
            name=self.name,
            content=f"[{self.name}] 제약 조건 설정 완료",
            role="assistant",
            metadata={"constraints": constraints, "violations": []},
        )

    def validate_ess(self, proposal: dict) -> tuple[dict, list[str]]:
        """ESS 제안 검증 및 클램핑."""
        errors = []
        action = proposal.get("action", "idle")
        power = _safe_float(proposal.get("power_kw", 0.0), 0.0)
        soc = _safe_float(proposal.get("soc_pct", 50.0), 50.0)

        if action == "charge":
            if soc >= self.ess_soc_max_pct:
                errors.append(f"ESS 충전 차단: SoC {soc}% >= 최대 {self.ess_soc_max_pct}%")
                return {**proposal, "action": "idle", "power_kw": 0.0, "soc_pct": soc}, errors
            power = min(power, self.max_charge_kw)
        elif action == "discharge":
            if soc <= self.ess_soc_min_pct:
                errors.append(f"ESS 방전 차단: SoC {soc}% <= 최소 {self.ess_soc_min_pct}%")
                return {**proposal, "action": "idle", "power_kw": 0.0, "soc_pct": soc}, errors
            power = min(power, self.max_discharge_kw)
        else:
            power = 0.0

        return {**proposal, "action": action, "power_kw": round(power, 2), "soc_pct": soc}, errors

    def validate_trade(self, proposal: dict) -> tuple[dict | None, list[str]]:
        """거래 제안 검증."""
        errors = []
        qty = _safe_float(proposal.get("bid_quantity_kw", 0.0), 0.0)
        price = _safe_float(proposal.get("bid_price", 0.0), 0.0)

        if qty < self.min_trade_kw:
            errors.append(f"거래 폐기: 수량 {qty} < 최소 {self.min_trade_kw} kW")
            return None, errors
        if price <= 0:
            errors.append(f"거래 폐기: 유효하지 않은 가격 {price}")
            return None, errors
        return proposal, errors

    def validate_dr(self, proposal: dict) -> tuple[dict | None, list[str]]:
        """DR 제안 검증."""
        errors = []
        reduction = _safe_float(proposal.get("recommended_reduction_kw", 0.0), 0.0)
        if reduction < 0:
            errors.append(f"DR 폐기: reduction {reduction} < 0")
            return None, errors
        return proposal, errors


# ─────────────────────────────────────────────────────────────────
# SmartSeller-Agent
# ─────────────────────────────────────────────────────────────────

class SmartSellerAgentAS(AgentBase):
    """
    잉여 에너지 판매 에이전트 (AgentScope 기반).

    페르소나: 커뮤니티 내 에너지 중개 트레이더
    sys_prompt를 통해 수익 극대화 목표와 전략이 주입됩니다.
    """

    def __init__(self, peak_risk_price_ratio: float = 0.90):
        super().__init__()
        self.name = "SmartSeller-Agent"
        self.sys_prompt = _PROMPTS["persona_smart_seller"]
        self.peak_risk_price_ratio = peak_risk_price_ratio

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        pass

    async def reply(self, msg: Msg) -> Msg:
        """
        State JSON을 분석하여 최적 입찰가/수량을 결정.

        msg.metadata['state'] 에서 State Translator JSON을 읽습니다.
        """
        state = (msg.metadata or {}).get("state", {})
        cs = state.get("community_state", {})
        ms = state.get("market_state", {})

        surplus = float(cs.get("surplus_energy", 0.0))
        grid_price = float(ms.get("grid_price") or 100.0)
        price_range = ms.get("community_trade_price_range", [80.0, 110.0])
        p2p_min, p2p_max = float(price_range[0]), float(price_range[1])
        peak_risk = cs.get("peak_risk", "LOW")
        time_str = state.get("time", "")

        if surplus <= 0:
            proposal = {
                "action": "hold",
                "bid_price": 0.0,
                "bid_quantity_kw": 0.0,
                "surplus_kw": 0.0,
                "reason": "잉여 에너지 없음 — 판매 보류",
            }
        else:
            if peak_risk == "HIGH":
                bid_price = round(p2p_max * self.peak_risk_price_ratio, 1)
            elif peak_risk == "MEDIUM":
                bid_price = round((p2p_min + p2p_max) / 2, 1)
            else:
                bid_price = p2p_min

            if bid_price < grid_price:
                action = "sell_p2p"
            else:
                action = "sell_grid"
                bid_price = round(grid_price * 0.95, 1)

            proposal = {
                "action": action,
                "bid_price": bid_price,
                "bid_quantity_kw": surplus,
                "surplus_kw": surplus,
                "timestamp": time_str,
                "reason": f"잉여 {surplus:.1f}kW, 피크위험={peak_risk} → {action} @{bid_price} 원/kWh",
            }

        return Msg(
            name=self.name,
            content=f"[{self.name}] {proposal['reason']}",
            role="assistant",
            metadata={"proposal": proposal},
        )


# ─────────────────────────────────────────────────────────────────
# StorageMaster-Agent
# ─────────────────────────────────────────────────────────────────

class StorageMasterAgentAS(AgentBase):
    """
    ESS 운영 최적화 에이전트 (AgentScope 기반).

    페르소나: 배터리 에너지 저장 시스템 운영 전문가
    sys_prompt를 통해 TOU 기반 충방전 전략 페르소나가 주입됩니다.
    """

    def __init__(
        self,
        price_charge_threshold: float = 85.0,
        price_discharge_threshold: float = 115.0,
    ):
        super().__init__()
        self.name = "StorageMaster-Agent"
        self.sys_prompt = _PROMPTS["persona_storage_master"]
        self.price_charge_threshold = price_charge_threshold
        self.price_discharge_threshold = price_discharge_threshold

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        pass

    async def reply(self, msg: Msg) -> Msg:
        """
        State JSON으로부터 ESS 충방전 결정.
        """
        state = (msg.metadata or {}).get("state", {})
        cs = state.get("community_state", {})
        ms = state.get("market_state", {})
        es = state.get("ess_state", {})

        soc_pct = es.get("soc")
        capacity = es.get("capacity")
        avail_discharge = float(es.get("available_discharge") or 0.0)

        if soc_pct is None or capacity is None:
            proposal = {"action": "idle", "power_kw": 0.0, "soc_pct": None, "reason": "ESS 미설치"}
        else:
            soc_pct = float(soc_pct)
            capacity = float(capacity)
            grid_price = float(ms.get("grid_price") or 100.0)
            surplus = float(cs.get("surplus_energy", 0.0))
            peak_risk = cs.get("peak_risk", "LOW")

            # 최대 ESS 기준 (Policy가 최종 클램핑)
            max_kw = capacity / 4

            if peak_risk == "HIGH" and soc_pct > 15:
                power = min(max_kw, avail_discharge / 0.25)
                proposal = {
                    "action": "discharge",
                    "power_kw": round(power, 2),
                    "soc_pct": soc_pct,
                    "reason": f"피크 위험 HIGH → 방전 {power:.1f}kW",
                }
            elif surplus > 1.0 and soc_pct < 90:
                power = min(max_kw, surplus)
                proposal = {
                    "action": "charge",
                    "power_kw": round(power, 2),
                    "soc_pct": soc_pct,
                    "reason": f"잉여 PV {surplus:.1f}kW 흡수 충전",
                }
            elif grid_price <= self.price_charge_threshold and soc_pct < 90:
                avail_kwh = (0.95 - soc_pct / 100) * capacity
                power = min(max_kw, avail_kwh / 0.25)
                proposal = {
                    "action": "charge",
                    "power_kw": round(power, 2),
                    "soc_pct": soc_pct,
                    "reason": f"TOU 저가 {grid_price} 원/kWh → 충전",
                }
            elif grid_price >= self.price_discharge_threshold and soc_pct > 15:
                power = min(max_kw, avail_discharge / 0.25)
                proposal = {
                    "action": "discharge",
                    "power_kw": round(power, 2),
                    "soc_pct": soc_pct,
                    "reason": f"TOU 고가 {grid_price} 원/kWh → 방전",
                }
            else:
                proposal = {
                    "action": "idle",
                    "power_kw": 0.0,
                    "soc_pct": soc_pct,
                    "reason": "대기 (조건 미충족)",
                }

        return Msg(
            name=self.name,
            content=f"[{self.name}] {proposal['reason']}",
            role="assistant",
            metadata={"proposal": proposal},
        )


# ─────────────────────────────────────────────────────────────────
# EcoSaver-Agent
# ─────────────────────────────────────────────────────────────────

class EcoSaverAgentAS(AgentBase):
    """
    수요반응(DR) 에이전트 (AgentScope 기반).

    페르소나: 에너지 효율 전문가 / 수요반응 코디네이터
    sys_prompt를 통해 소비 절감 및 DR 권고 페르소나가 주입됩니다.
    """

    def __init__(self, peak_threshold_kw: float = 500.0, reduction_factor: float = 0.30):
        super().__init__()
        self.name = "EcoSaver-Agent"
        self.sys_prompt = _PROMPTS["persona_eco_saver"]
        self.peak_threshold_kw = peak_threshold_kw
        self.reduction_factor = reduction_factor

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        pass

    async def reply(self, msg: Msg) -> Msg:
        """
        피크 초과 상황에서 DR 이벤트 생성.
        """
        state = (msg.metadata or {}).get("state", {})
        cs = state.get("community_state", {})
        total_load = float(cs.get("total_load", 0.0))
        peak_risk = cs.get("peak_risk", "LOW")
        time_str = state.get("time", "")

        dr_events = []

        if total_load > self.peak_threshold_kw:
            excess = total_load - self.peak_threshold_kw
            reduction = round(excess * self.reduction_factor, 2)
            dr_events.append({
                "timestamp": time_str,
                "net_load_kw": round(total_load, 2),
                "recommended_reduction_kw": reduction,
                "action": "demand_response",
                "reason": f"피크 초과 {excess:.1f}kW → {self.reduction_factor*100:.0f}% 절감 권고",
            })
        elif peak_risk == "MEDIUM":
            reduction = round(total_load * 0.05, 2)
            dr_events.append({
                "timestamp": time_str,
                "net_load_kw": round(total_load, 2),
                "recommended_reduction_kw": reduction,
                "action": "demand_response",
                "reason": "피크 위험 MEDIUM → 예방적 5% 절감 권고",
            })

        n = len(dr_events)
        content = f"[{self.name}] DR 이벤트 {n}건 생성" if n else f"[{self.name}] DR 불필요"

        return Msg(
            name=self.name,
            content=content,
            role="assistant",
            metadata={"proposal": {"dr_events": dr_events}},
        )


# ─────────────────────────────────────────────────────────────────
# MarketCoordinator-Agent
# ─────────────────────────────────────────────────────────────────

class MarketCoordinatorAgentAS(AgentBase):
    """
    협상 조율 에이전트 (AgentScope 기반).

    페르소나: 에너지 커뮤니티 운영 총괄 코디네이터
    sys_prompt를 통해 조율자·중재자 페르소나가 주입됩니다.

    SmartSeller / StorageMaster / EcoSaver 제안을 수신하여:
    - 충돌 해결 (HIGH peak: 방전 > 판매)
    - Policy 검증 적용
    - 최종 decisions 생성
    """

    def __init__(self, policy_agent: PolicyAgentAS):
        super().__init__()
        self.name = "MarketCoordinator-Agent"
        self.sys_prompt = _PROMPTS["persona_market_coordinator"]
        self.policy = policy_agent

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        pass

    async def reply(
        self,
        msg: Msg,
        seller_msg: Msg | None = None,
        storage_msg: Msg | None = None,
        eco_msg: Msg | None = None,
    ) -> Msg:
        """
        각 에이전트 Msg를 입력받아 조율 후 최종 decisions 반환.

        msg        : 상태 메시지 (State JSON 포함)
        seller_msg : SmartSeller 제안
        storage_msg: StorageMaster 제안
        eco_msg    : EcoSaver 제안
        """
        state = (msg.metadata or {}).get("state", {})
        cs = state.get("community_state", {})
        peak_risk = cs.get("peak_risk", "LOW")
        time_str = state.get("time", "")

        # ── 제안 추출 ─────────────────────────────────────────
        seller_proposal = (seller_msg.metadata or {}).get("proposal", {}) if seller_msg else {}
        storage_proposal = (storage_msg.metadata or {}).get("proposal", {}) if storage_msg else {}
        dr_proposals = ((eco_msg.metadata or {}).get("proposal", {}) or {}).get("dr_events", []) if eco_msg else []

        # ── Policy 검증 ───────────────────────────────────────
        all_violations = []
        conflict_notes: list[str] = []

        validated_storage, ess_errs = self.policy.validate_ess(storage_proposal)
        all_violations.extend(ess_errs)

        validated_seller, trade_errs = self.policy.validate_trade(seller_proposal)
        all_violations.extend(trade_errs)

        validated_dr = []
        for dr in dr_proposals:
            v, errs = self.policy.validate_dr(dr)
            all_violations.extend(errs)
            if v:
                validated_dr.append(v)

        # ── 충돌 해결 ─────────────────────────────────────────
        ess_action = validated_storage.get("action", "idle")
        ess_power = float(validated_storage.get("power_kw", 0.0))

        if peak_risk == "HIGH" and validated_seller is not None:
            if ess_action == "discharge":
                validated_seller = None
                note = "HIGH 피크: ESS 방전 우선, P2P 판매 보류"
                all_violations.append(note)
                conflict_notes.append(note)
            elif ess_action == "charge":
                sell_qty = float(validated_seller.get("bid_quantity_kw", 0))
                if sell_qty > ess_power:
                    adjusted_qty = round(sell_qty - ess_power, 2)
                    validated_seller = {
                        **validated_seller,
                        "bid_quantity_kw": adjusted_qty,
                    }
                    conflict_notes.append(
                        f"HIGH 피크 충전 병행: 판매수량 {sell_qty:.2f}→{adjusted_qty:.2f}kW 조정"
                    )

        # ── 최종 decisions 구성 ───────────────────────────────
        ess_schedule = [{
            "timestamp": time_str,
            "action": ess_action,
            "power_kw": ess_power,
            "soc_kwh": 0.0,
            "net_load_kw": float(cs.get("total_load", 0)) - float(cs.get("pv_generation", 0)),
            "reason": validated_storage.get("reason", ""),
        }]

        trading_recommendations = []
        if validated_seller and validated_seller.get("action") in ("sell_p2p", "sell_grid"):
            trading_recommendations.append({
                "timestamp": time_str,
                "surplus_kw": float(validated_seller.get("bid_quantity_kw", 0)),
                "bid_price": float(validated_seller.get("bid_price", 0)),
                "action": validated_seller.get("action", "sell_p2p"),
            })

        trading_evidence = [
            _build_trading_evidence_entry(
                state_json=state,
                seller_proposal=seller_proposal,
                validated_seller=validated_seller,
                storage_proposal=storage_proposal,
                validated_storage=validated_storage,
                dr_proposals=dr_proposals,
                validated_dr=validated_dr,
                policy_violations=all_violations,
                conflict_notes=conflict_notes,
                trading_recommendations=trading_recommendations,
            )
        ]

        decisions = {
            "ess_schedule": ess_schedule,
            "ess_summary": {
                "action": ess_action,
                "power_kw": ess_power,
                "peak_risk": peak_risk,
                "storage_reason": validated_storage.get("reason", ""),
            },
            "trading_recommendations": trading_recommendations,
            "trading_summary": {
                "total_surplus_events": len(trading_recommendations),
                "total_surplus_kw": round(
                    sum(r["surplus_kw"] for r in trading_recommendations), 2
                ),
            },
            "trading_evidence": trading_evidence,
            "demand_response_events": validated_dr,
            "dr_summary": {
                "dr_event_count": len(validated_dr),
                "total_reduction_kw": round(
                    sum(float(d.get("recommended_reduction_kw", 0)) for d in validated_dr), 2
                ),
            },
            "policy_violations": all_violations,
            "coordinator_notes": (
                f"[{self.name}] peak_risk={peak_risk}, "
                f"ESS={ess_action} {ess_power}kW, "
                f"trades={len(trading_recommendations)}, DR={len(validated_dr)}"
            ),
        }

        return Msg(
            name=self.name,
            content=decisions["coordinator_notes"],
            role="assistant",
            metadata={"decisions": decisions},
        )


# ─────────────────────────────────────────────────────────────────
# AgentScope 초기화
# ─────────────────────────────────────────────────────────────────

_agentscope_initialized = False


def _build_trading_evidence_entry(
    state_json: dict,
    seller_proposal: dict,
    validated_seller: dict | None,
    storage_proposal: dict,
    validated_storage: dict,
    dr_proposals: list[dict],
    validated_dr: list[dict],
    policy_violations: list[str],
    conflict_notes: list[str],
    trading_recommendations: list[dict],
) -> dict:
    """Step3 AgentScope 전력거래 의사결정 근거를 UI 표시용 구조로 정규화."""
    cs = state_json.get("community_state") or {}
    ms = state_json.get("market_state") or {}
    final_trade = trading_recommendations[0] if trading_recommendations else {}
    return {
        "timestamp": state_json.get("timestamp") or state_json.get("time", ""),
        "time": state_json.get("time", ""),
        "peak_risk": cs.get("peak_risk", "LOW"),
        "total_load_kw": float(cs.get("total_load", 0.0) or 0.0),
        "pv_generation_kw": float(cs.get("pv_generation", 0.0) or 0.0),
        "surplus_energy_kw": float(cs.get("surplus_energy", 0.0) or 0.0),
        "deficit_energy_kw": float(cs.get("deficit_energy", 0.0) or 0.0),
        "grid_price": float(ms.get("grid_price", 0.0) or 0.0),
        "community_trade_price_range": list(ms.get("community_trade_price_range") or []),
        "seller_proposal": seller_proposal or {},
        "validated_seller": validated_seller or {},
        "storage_proposal": storage_proposal or {},
        "validated_storage": validated_storage or {},
        "dr_proposals": dr_proposals or [],
        "validated_dr": validated_dr or [],
        "policy_violations": list(policy_violations or []),
        "conflict_resolution": list(conflict_notes or []),
        "final_trading_action": final_trade.get("action", "hold"),
        "final_bid_price": float(final_trade.get("bid_price", 0.0) or 0.0),
        "final_surplus_kw": float(final_trade.get("surplus_kw", 0.0) or 0.0),
        "final_reason": (
            (validated_seller or {}).get("reason")
            or "; ".join(conflict_notes or [])
            or ("거래 권고 없음" if not trading_recommendations else "거래 권고 생성")
        ),
    }


def _init_agentscope() -> None:
    global _agentscope_initialized
    if not _agentscope_initialized:
        agentscope.init(project="SEAPAC-MultiAgent")
        _agentscope_initialized = True


# ─────────────────────────────────────────────────────────────────
# 단일 스텝 실행 (async 내부)
# ─────────────────────────────────────────────────────────────────

async def _run_single_step_async(
    state_json: dict,
    policy: PolicyAgentAS,
    seller: SmartSellerAgentAS,
    storage: StorageMasterAgentAS,
    eco_saver: EcoSaverAgentAS,
    coordinator: MarketCoordinatorAgentAS,
) -> dict:
    """
    AgentScope MsgHub를 통해 단일 스텝 의사결정 실행.

    1) 상태 메시지를 MsgHub로 브로드캐스트
    2) 각 에이전트가 reply()로 제안 반환
    3) MarketCoordinator가 제안을 조율하여 decisions 반환
    """
    _tmpl = _PROMPTS["state_message_template"]
    _cs = state_json.get("community_state") or {}
    state_msg = Msg(
        name="StateTranslator",
        content=_tmpl.format(
            time=state_json.get("time", "?"),
            total_load=_cs.get("total_load", 0),
            peak_risk=_cs.get("peak_risk", "N/A"),
        ),
        role="user",
        metadata={"state": state_json},
    )

    async with MsgHub(
        participants=[policy, seller, storage, eco_saver, coordinator],
        announcement=state_msg,
        enable_auto_broadcast=False,
    ):
        policy_msg  = await policy(state_msg)
        seller_msg  = await seller(state_msg)
        storage_msg = await storage(state_msg)
        eco_msg     = await eco_saver(state_msg)

        final_msg = await coordinator.reply(
            state_msg,
            seller_msg=seller_msg,
            storage_msg=storage_msg,
            eco_msg=eco_msg,
        )

    return (final_msg.metadata or {}).get("decisions", {})


# ─────────────────────────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────────────────────────

def run_agentscope_decision(
    state_json: dict,
    peak_threshold_kw: float = 500.0,
    price_charge_threshold: float = 85.0,
    price_discharge_threshold: float = 115.0,
    max_charge_kw: float = 50.0,
    max_discharge_kw: float = 50.0,
) -> dict:
    """
    AgentScope 기반 Multi-Agent Decision Engine — 단일 스텝.

    PRD Step 3: State Translator JSON → 5개 에이전트 → decisions

    Args:
        state_json: Step 2 State Translator 출력
        peak_threshold_kw: EcoSaver 피크 임계값
        price_charge_threshold: StorageMaster TOU 충전 단가 기준 (원/kWh)
        price_discharge_threshold: StorageMaster TOU 방전 단가 기준 (원/kWh)
        max_charge_kw: Policy ESS 최대 충전 전력
        max_discharge_kw: Policy ESS 최대 방전 전력

    Returns:
        decisions dict (ess_schedule, trading_recommendations, demand_response_events)
    """
    _init_agentscope()

    policy      = PolicyAgentAS(max_charge_kw=max_charge_kw, max_discharge_kw=max_discharge_kw)
    seller      = SmartSellerAgentAS()
    storage     = StorageMasterAgentAS(price_charge_threshold, price_discharge_threshold)
    eco_saver   = EcoSaverAgentAS(peak_threshold_kw)
    coordinator = MarketCoordinatorAgentAS(policy)

    decisions = asyncio.run(
        _run_single_step_async(state_json, policy, seller, storage, eco_saver, coordinator)
    )
    # Self-Critic Agent: LLM이 자기 전략을 반박하도록 설계 (단일 스텝)
    from seapac_agents.self_critic import run_self_critic
    try:
        self_critic_out = run_self_critic(decisions, state_context=None, use_llm=True)
    except Exception:
        self_critic_out = run_self_critic(decisions, use_llm=False)
    return {**(decisions or {}), "self_critic_output": self_critic_out.to_dict()}


def run_agentscope_decision_series(
    state_json_list: list[dict],
    peak_threshold_kw: float = 500.0,
    price_charge_threshold: float = 85.0,
    price_discharge_threshold: float = 115.0,
    max_charge_kw: float = 50.0,
    max_discharge_kw: float = 50.0,
) -> dict:
    """
    AgentScope 기반 Multi-Agent Decision Engine — 다중 스텝 일괄 처리.

    translate_dataframe() 출력 리스트를 받아
    Step 4 run_execution()이 기대하는 decisions 형식으로 합산합니다.

    Returns:
        decisions: ess_schedule(list), trading_recommendations(list), demand_response_events(list)
    """
    _init_agentscope()

    policy      = PolicyAgentAS(max_charge_kw=max_charge_kw, max_discharge_kw=max_discharge_kw)
    seller      = SmartSellerAgentAS()
    storage     = StorageMasterAgentAS(price_charge_threshold, price_discharge_threshold)
    eco_saver   = EcoSaverAgentAS(peak_threshold_kw)
    coordinator = MarketCoordinatorAgentAS(policy)

    async def _run_all():
        ess_schedule: list[dict] = []
        trading_recommendations: list[dict] = []
        trading_evidence: list[dict] = []
        demand_response_events: list[dict] = []

        for state in state_json_list:
            d = await _run_single_step_async(
                state, policy, seller, storage, eco_saver, coordinator
            )
            ess_schedule.extend(d.get("ess_schedule", []))
            trading_recommendations.extend(d.get("trading_recommendations", []))
            trading_evidence.extend(d.get("trading_evidence", []))
            demand_response_events.extend(d.get("demand_response_events", []))

        decisions = {
            "ess_schedule": ess_schedule,
            "trading_recommendations": trading_recommendations,
            "trading_evidence": trading_evidence,
            "demand_response_events": demand_response_events,
            "ess_summary": {"total_steps": len(ess_schedule)},
            "trading_summary": {
                "total_surplus_events": len(trading_recommendations),
                "total_surplus_kw": round(
                    sum(float(r.get("surplus_kw", 0)) for r in trading_recommendations), 2
                ),
            },
            "dr_summary": {"dr_event_count": len(demand_response_events)},
        }
        # Self-Critic Agent: LLM이 자기 전략을 반박하도록 설계
        from seapac_agents.self_critic import run_self_critic, SelfCriticOutput
        try:
            self_critic_out = run_self_critic(decisions, state_context=None, use_llm=True)
        except Exception:
            self_critic_out = run_self_critic(decisions, use_llm=False)
        decisions["self_critic_output"] = self_critic_out.to_dict()
        return decisions

    return asyncio.run(_run_all())
