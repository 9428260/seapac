"""
Audit logging for Parallel Execution Layer (PRD §11 — seapac_parallel_agents_prd.md).

Every decision must be logged: candidate actions, policy evaluation, storage feasibility, final results.
Logs are append-only (immutable) — one record per evaluation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def log_parallel_evaluation(
    bundle: dict,
    orchestrator_output: dict,
    decisions_after: dict,
    *,
    audit_path: str | Path | None = None,
    run_id: str | None = None,
) -> None:
    """
    Append one immutable audit record for a parallel evaluation run.
    Record includes: timestamp, run_id, candidate_actions, policy/storage/eco summary, final approved.
    """
    if audit_path is None:
        return
    path = Path(audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_id": run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        "site_state_summary": {
            "load_kw": (bundle.get("site_state") or {}).get("load_kw"),
            "pv_kw": (bundle.get("site_state") or {}).get("pv_kw"),
            "ess_soc": (bundle.get("site_state") or {}).get("ess_soc"),
        },
        "candidate_action_count": len(bundle.get("candidate_actions") or []),
        "candidate_action_ids": [a.get("action_id") for a in (bundle.get("candidate_actions") or []) if a.get("action_id")],
        "policy": {
            "approved_count": len(orchestrator_output.get("approved_actions") or []),
            "rejected_count": len(orchestrator_output.get("rejected_actions") or []),
            "violation_count": len(orchestrator_output.get("policy_violation_report") or []),
            "risk_score": orchestrator_output.get("risk_score"),
        },
        "recommendations_count": len(orchestrator_output.get("recommendations") or []),
        "final_approved_actions": list(orchestrator_output.get("approved_actions") or []),
        "final_ess_count": len(decisions_after.get("ess_schedule") or []),
        "final_trade_count": len(decisions_after.get("trading_recommendations") or []),
        "final_dr_count": len(decisions_after.get("demand_response_events") or []),
    }

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
