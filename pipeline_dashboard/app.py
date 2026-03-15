"""
Flask UI for SEAPAC pipeline runs and stage results.

Run:
  export PIPELINE_DB_DIR=output   # optional, default: output
  python -m pipeline_dashboard.app
  # or: flask --app pipeline_dashboard.app run
  # Open http://127.0.0.1:5001 (default port 5001; macOS uses 5000 for AirPlay)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, abort, request, jsonify, redirect, url_for

from pipeline_dashboard.db import (
    get_db_path,
    init_db,
    create_run,
    get_runs,
    get_run_with_stages,
    get_alfp_agent_steps,
    get_alfp_domain_steps,
)

# 프로젝트 루트 (run_full_pipeline.py 가 있는 디렉터리)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FORMAT = "%(asctime)s  %(levelname)-7s  %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _setup_flask_logging() -> None:
    """Flask 실행 로그를 logs/ 디렉터리 파일 + 콘솔에 남깁니다. (root 로거는 건드리지 않아 Werkzeug 동작 유지)"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"dashboard_{datetime.now().strftime('%Y%m%d')}.log"
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    app_logger = logging.getLogger("pipeline_dashboard")
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = True
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    app_logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    app_logger.addHandler(ch)


_setup_flask_logging()
log = logging.getLogger("pipeline_dashboard")

app = Flask(__name__, template_folder=Path(__file__).parent / "templates")


def _format_thousands(value) -> str:
    """Jinja filter: format number with thousands separator (e.g. 1213867 → 1,213,867)."""
    if value is None:
        return "0"
    try:
        return "{:,.0f}".format(float(value))
    except (TypeError, ValueError):
        return str(value)


app.jinja_env.filters["int_fmt"] = _format_thousands


@app.before_request
def _log_request():
    """요청 시작 시 로그 (실행 로그 확인용)."""
    log.info("Request  %s  %s", request.method, request.path)


def _output_dir() -> str:
    """Return output directory name (e.g. 'output'). Used for subprocess and env."""
    return os.environ.get("PIPELINE_DB_DIR", "output")


def _db_path() -> Path:
    """Return absolute path to pipeline_runs.db so Dashboard and subprocess use the same file."""
    db_dir = _output_dir()
    # 프로젝트 루트 기준 절대 경로 사용 (실행 cwd와 무관하게 동일 DB 참조)
    return get_db_path(PROJECT_ROOT / db_dir)


def _measure_date_from_data(data_path: str) -> str | None:
    """
    Load the pipeline data pkl and extract the measure date (YYYY-MM-DD).
    Uses metadata.period_start or the first timestamp in timeseries.
    Returns None on any error or missing data.
    """
    path = PROJECT_ROOT / data_path
    if not path.is_file():
        return None
    try:
        import pickle
        with open(path, "rb") as f:
            data = pickle.load(f)
    except Exception:
        return None
    meta = data.get("metadata") if isinstance(data, dict) else None
    if isinstance(meta, dict):
        period_start = meta.get("period_start")
        if period_start is not None:
            s = str(period_start).strip()
            if len(s) >= 10 and s[:10].replace("-", "").isdigit():
                return s[:10]
    ts = data.get("timeseries") if isinstance(data, dict) else None
    if ts is not None and hasattr(ts, "iloc") and "timestamp" in getattr(ts, "columns", []):
        try:
            import pandas as pd
            first = ts["timestamp"].iloc[0]
            t = pd.Timestamp(first)
            return t.strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


@app.route("/")
def index():
    """List recent pipeline runs and show run form. Supports search: prosumer, measure_date, run_date."""
    init_db(_db_path())
    search_prosumer = (request.args.get("prosumer") or "").strip()
    search_measure_date = (request.args.get("measure_date") or "").strip()
    search_run_date = (request.args.get("run_date") or "").strip()
    runs = get_runs(
        limit=50,
        prosumer=search_prosumer or None,
        measure_date=search_measure_date or None,
        run_date=search_run_date or None,
        db_path=_db_path(),
    )
    # 프로슈머 콤보박스용 목록: DB에서 사용된 프로슈머 + 기본값
    all_for_prosumers = get_runs(limit=500, db_path=_db_path())
    prosumer_set = {r.get("args", {}).get("prosumer") for r in all_for_prosumers if (r.get("args") or {}).get("prosumer")}
    prosumer_set.add("bus_48_Commercial")
    prosumer_options = sorted(prosumer_set)
    current_tab = request.args.get("tab", "")
    # LNB 단계별 이력 탭 선택 시 해당 탭 내용을 보려면 최신 run 상세로 이동
    if current_tab in ("1", "2", "3", "4", "5", "6") and runs:
        return redirect(url_for("run_detail", run_id=runs[0]["id"]) + "?tab=" + current_tab)
    return render_template(
        "index.html",
        runs=runs,
        run=None,
        current_tab=current_tab,
        current_path=request.path,
        search_prosumer=search_prosumer,
        search_measure_date=search_measure_date,
        search_run_date=search_run_date,
        prosumer_options=prosumer_options,
    )


@app.route("/api/prosumers")
def api_prosumers():
    """
    GET ?data_path=data/test_5days.pkl
    Load the pkl and return list of prosumer_id. Returns [] if file missing or invalid.
    """
    data_path = (request.args.get("data_path") or "data/test_5days.pkl").strip()
    path = PROJECT_ROOT / data_path
    if not path.is_file():
        return jsonify({"prosumers": []})
    try:
        from alfp.data.loader import load_dataset, get_prosumer_list
        data = load_dataset(str(path))
        prosumers = get_prosumer_list(data)
        return jsonify({"prosumers": prosumers})
    except Exception as e:
        log.warning("api_prosumers data_path=%s error: %s", data_path, e)
        return jsonify({"prosumers": []})


@app.route("/api/run", methods=["POST"])
def api_run():
    """
    Request body (JSON): data_path, steps, prosumer (single) or prosumers (list), use_parallel, phase, ...
    Creates one run per prosumer, starts pipeline subprocess per run, returns run_id or run_ids.
    """
    out_dir = _output_dir()
    data = request.get_json(force=True, silent=True) or {}
    data_path = data.get("data_path", "data/test_5days.pkl")
    steps = int(data.get("steps", 96))  # 96 고정 (화면에서 제거됨)
    # Support multiple: prosumers (list) or single prosumer (string)
    prosumers_raw = data.get("prosumers")
    if isinstance(prosumers_raw, list) and len(prosumers_raw) > 0:
        prosumers = [str(p).strip() for p in prosumers_raw if str(p).strip()]
    else:
        single = (data.get("prosumer") or "bus_48_Commercial").strip()
        prosumers = [single] if single else ["bus_48_Commercial"]
    use_parallel = bool(data.get("use_parallel", True))  # 기본 사용 (화면에서 제거됨)
    phase = int(data.get("phase", 4))
    skip_alfp = bool(data.get("skip_alfp", False))
    measure_date = (data.get("measure_date") or "").strip() or None
    run_date = (data.get("run_date") or "").strip() or None
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if not run_date:
        run_date = today
    # 기준일자: 폼에서 설정하지 않으면 5월 5일 기본값 (해당 날짜 데이터로 파이프라인 실행)
    if not measure_date:
        measure_date = "2026-05-05"

    init_db(_db_path())
    out_dir_abs = str(PROJECT_ROOT / out_dir)
    prev_pp = os.environ.get("PYTHONPATH", "")

    is_p2p_mode = len(prosumers) > 1

    if is_p2p_mode:
        # ── P2P 거래 모드: 단일 run + --prosumers 다중값으로 하나의 파이프라인 실행 ──
        args_dict = {
            "data_path": data_path,
            "steps": steps,
            "prosumer": prosumers[0],       # 대표 ID (검색용)
            "prosumers": prosumers,         # 실제 다중 목록
            "use_parallel": use_parallel,
            "phase": phase,
            "skip_alfp": skip_alfp,
            "output_dir": out_dir,
            "save_json": True,
            "measure_date": measure_date,
            "run_date": run_date,
            "p2p_mode": True,
        }
        run_id = create_run(args_dict, db_path=_db_path())

        cmd = [
            sys.executable,
            "-m",
            "run_full_pipeline",
            "--data-path", data_path,
            "--measure-date", measure_date,
            "--steps", str(steps),
            "--prosumers", *prosumers,
            "--phase", str(phase),
            "--output-dir", out_dir_abs,
            "--save-json",
        ]
        if use_parallel:
            cmd.append("--use-parallel")
        if skip_alfp:
            cmd.append("--skip-alfp")

        env = os.environ.copy()
        env["PIPELINE_RUN_ID"] = str(run_id)
        env["PIPELINE_DB_DIR"] = out_dir
        env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + prev_pp if prev_pp else "")

        subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return jsonify({"run_id": run_id, "p2p_mode": True, "prosumers": prosumers})

    else:
        # ── 단일 프로슈머 모드 (기존 동작 유지) ──────────────────────
        prosumer = prosumers[0]
        args_dict = {
            "data_path": data_path,
            "steps": steps,
            "prosumer": prosumer,
            "use_parallel": use_parallel,
            "phase": phase,
            "skip_alfp": skip_alfp,
            "output_dir": out_dir,
            "save_json": True,
            "measure_date": measure_date,
            "run_date": run_date,
        }
        run_id = create_run(args_dict, db_path=_db_path())

        cmd = [
            sys.executable,
            "-m",
            "run_full_pipeline",
            "--data-path", data_path,
            "--measure-date", measure_date,
            "--steps", str(steps),
            "--prosumer", prosumer,
            "--phase", str(phase),
            "--output-dir", out_dir_abs,
            "--save-json",
        ]
        if use_parallel:
            cmd.append("--use-parallel")
        if skip_alfp:
            cmd.append("--skip-alfp")

        env = os.environ.copy()
        env["PIPELINE_RUN_ID"] = str(run_id)
        env["PIPELINE_DB_DIR"] = out_dir
        env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + prev_pp if prev_pp else "")

        subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return jsonify({"run_id": run_id})


def _stages_for_tabs(stages: list[dict]) -> dict[str, list]:
    """Group stages by tab (1–6) for run detail view. Uses string containment, no Jinja2 search test."""
    tab1 = [s for s in stages if "ALFP" in s.get("stage_name", "")]
    tab2 = [s for s in stages if "시뮬레이션 실행" in s.get("stage_name", "")]
    tab3 = [
        s for s in stages
        if "State Translator" in s.get("stage_name", "")
        or "Step3  AgentScope" in s.get("stage_name", "")
        or "병합" in s.get("stage_name", "")
    ]
    tab4 = [s for s in stages if "전력거래" in s.get("stage_name", "")]
    if not tab4:
        tab4 = [s for s in stages if "Step4  Action Execution" in s.get("stage_name", "")]
    tab5 = [
        s for s in stages
        if ("Step3.5 Parallel" in s.get("stage_name", "") and "Thread A" in s.get("stage_name", ""))
        or "Policy / EcoSaver / Storage" in s.get("stage_name", "")
    ]
    tab6 = [s for s in stages if "Step5" in s.get("stage_name", "")]
    return {
        "tab1_stages": tab1,
        "tab2_stages": tab2,
        "tab3_stages": tab3,
        "tab4_stages": tab4,
        "tab5_stages": tab5,
        "tab6_stages": tab6,
    }


def _prosumer_options(db_path: Path):
    all_runs = get_runs(limit=500, db_path=db_path)
    prosumer_set = {r.get("args", {}).get("prosumer") for r in all_runs if (r.get("args") or {}).get("prosumer")}
    prosumer_set.add("bus_48_Commercial")
    return sorted(prosumer_set)


@app.route("/agent-plans")
def agent_plans():
    """Agents 실행 계획 페이지: 상단 검색 + 전력거래/EcoSaver/Storage/Policy 계획 표시."""
    init_db(_db_path())
    search_run_id = request.args.get("run_id", "").strip()
    search_plan_type = (request.args.get("plan_type") or "all").strip()
    search_measure_date = (request.args.get("measure_date") or "").strip()
    search_run_date = (request.args.get("run_date") or "").strip()
    runs = get_runs(
        limit=50,
        measure_date=search_measure_date or None,
        run_date=search_run_date or None,
        db_path=_db_path(),
    )
    run = None
    plan_sections = {
        "trading": {"title": "전력거래 계획", "content": None, "visible": search_plan_type in ("all", "trading")},
        "eco_saver": {"title": "EcoSaver 계획", "content": None, "visible": search_plan_type in ("all", "eco_saver")},
        "storage": {"title": "Storage 관리 계획", "content": None, "visible": search_plan_type in ("all", "storage")},
        "policy": {"title": "Policy 계획", "content": None, "visible": search_plan_type in ("all", "policy")},
    }
    if search_run_id:
        try:
            rid = int(search_run_id)
            run = get_run_with_stages(rid, db_path=_db_path())
        except (ValueError, TypeError):
            pass
    if run and run.get("stages"):
        stages = run["stages"]
        tab5 = [s for s in stages if "Step3.5 Parallel" in s.get("stage_name", "") or "Policy / EcoSaver / Storage" in s.get("stage_name", "")]
        step3 = [s for s in stages if "Step3  AgentScope" in s.get("stage_name", "")]
        pa_summary = (tab5[0].get("summary") or {}) if tab5 else {}
        step3_summary = (step3[0].get("summary") or {}) if step3 else {}
        plan_sections["trading"]["content"] = {
            "objective": step3_summary.get("결정 모드") or pa_summary.get("실행 방식") or "전력거래 최적화: Policy 제약 → ESS 스케줄 → DR 이벤트 → 시뮬레이션 검증",
            "거래 권고": step3_summary.get("거래 권고", "—"),
        }
        plan_sections["eco_saver"]["content"] = {
            "권고": pa_summary.get("EcoSaver 권고", "—"),
            "설명": "수요반응(DR) 이벤트 생성. 피크 초과 시 절감 권고.",
        }
        plan_sections["storage"]["content"] = {
            "ESS 스케줄": step3_summary.get("ESS 스케줄", pa_summary.get("ESS 스케줄", "—")),
            "승인": pa_summary.get("승인 액션", "—"),
            "거절": pa_summary.get("거절 액션", "—"),
            "수정": pa_summary.get("수정 액션", "—"),
        }
        plan_sections["policy"]["content"] = {
            "정책 위반": pa_summary.get("정책 위반", "—"),
            "위험 점수": pa_summary.get("위험 점수", "—"),
            "설명": "제약 조건 설정 및 검증. 정책·규제 준수 검증.",
        }
    else:
        # 기본 설명 문구 (Run 미선택 시)
        plan_sections["trading"]["content"] = {"objective": "전력거래 최적화: Policy 제약 → ESS 스케줄 → DR 이벤트 → 시뮬레이션 검증", "거래 권고": "Run을 선택하면 Step3/Step3.5 결과가 표시됩니다."}
        plan_sections["eco_saver"]["content"] = {"권고": "—", "설명": "수요반응(DR) 이벤트 생성. 피크 초과 시 절감 권고."}
        plan_sections["storage"]["content"] = {"ESS 스케줄": "—", "승인": "—", "거절": "—", "수정": "—"}
        plan_sections["policy"]["content"] = {"정책 위반": "—", "위험 점수": "—", "설명": "제약 조건 설정 및 검증. 정책·규제 준수 검증."}
    prosumer_set = {r.get("args", {}).get("prosumer") for r in runs if (r.get("args") or {}).get("prosumer")}
    prosumer_set.add("bus_48_Commercial")
    prosumer_options = sorted(prosumer_set)
    return render_template(
        "agent_plans.html",
        runs=runs,
        run=run,
        current_path=request.path,
        search_run_id=search_run_id,
        search_plan_type=search_plan_type,
        search_measure_date=search_measure_date,
        search_run_date=search_run_date,
        plan_sections=plan_sections,
        prosumer_options=prosumer_options,
    )


@app.route("/runs/by-search")
def run_detail_by_search():
    """Find first run matching prosumer/measure_date/run_date and redirect to its detail with given tab."""
    init_db(_db_path())
    tab = request.args.get("tab", "1")
    run_date = (request.args.get("run_date") or "").strip()
    measure_date = (request.args.get("measure_date") or "").strip()
    prosumer = (request.args.get("prosumer") or "").strip()
    runs = get_runs(
        limit=1,
        prosumer=prosumer or None,
        measure_date=measure_date or None,
        run_date=run_date or None,
        db_path=_db_path(),
    )
    if not runs:
        runs = get_runs(limit=1, db_path=_db_path())
    if runs:
        return redirect(url_for("run_detail", run_id=runs[0]["id"]) + "?tab=" + tab)
    return redirect(url_for("index") + "#history")


@app.route("/api/runs/<int:run_id>/mesa_trajectory")
def api_mesa_trajectory(run_id: int):
    """MESA 시뮬레이션 스텝별 궤적(지표) JSON. Dashboard에서 그리드/궤적 차트용."""
    path = PROJECT_ROOT / _output_dir() / f"run_{run_id}_mesa_trajectory.json"
    if not path.is_file():
        return jsonify({"error": "not_found", "message": "MESA 궤적 데이터 없음"}), 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify({"trajectory": data})
    except Exception as e:
        log.warning("mesa_trajectory read error run_id=%s: %s", run_id, e)
        return jsonify({"error": "read_error", "message": str(e)}), 500


@app.route("/runs/<int:run_id>")
def run_detail(run_id: int):
    """Show one run and its stages (architecture step results). Langchain DeepAgent 단계는 탭1에서 조회."""
    init_db(_db_path())
    run = get_run_with_stages(run_id, db_path=_db_path())
    if run is None:
        abort(404)
    stages = run.get("stages", [])
    tabs = _stages_for_tabs(stages)
    # ALFP(Stage 1) 실행 시 DB에 기록된 Agent별 단계 로그 조회 (docs/alfp/ALFP_AGENT_STEP_LOGGING.md)
    db_path = _db_path()
    alfp_agent_steps = get_alfp_agent_steps(run_id, stage_order=1, db_path=db_path)
    if alfp_agent_steps is None:
        alfp_agent_steps = []
    alfp_domain_steps = get_alfp_domain_steps(run_id, stage_order=1, db_path=db_path)
    if alfp_domain_steps is None:
        alfp_domain_steps = []
    log.info("run_detail run_id=%s alfp_agent_steps count=%s domain_steps=%s db=%s", run_id, len(alfp_agent_steps), len(alfp_domain_steps), db_path)
    current_tab = request.args.get("tab", "1")
    # ALFP 탭(1) 내 서브탭: summary | domain | steps | result (refresh 후에도 유지)
    current_sub = request.args.get("sub", "summary")
    if current_sub not in ("summary", "domain", "steps", "result"):
        current_sub = "summary"
    # CDA 탭(4) 내 서브탭: exec | domain (실행 / 도메인 특화)
    current_sub4 = request.args.get("sub4", "exec")
    if current_sub4 not in ("exec", "domain"):
        current_sub4 = "exec"
    # CDA 도메인 특화: Strategy Agent · Negotiation 로그 (Step3 stage summary에서 추출)
    cda_strategy_logs = []
    cda_negotiation_logs = []
    for s in stages:
        summary = s.get("summary") or {}
        if summary.get("strategy_reasoning_logs"):
            cda_strategy_logs = summary["strategy_reasoning_logs"]
        if summary.get("negotiation_logs"):
            cda_negotiation_logs = summary["negotiation_logs"]

    # AgentScope Step3 — 실행된 5개 에이전트 요약 (seapac_agents/decision.py 기준)
    agentscope_agent_summary = []
    agentscope_step3_stage = None
    for s in stages:
        if "Step3  AgentScope" in s.get("stage_name", ""):
            agentscope_step3_stage = s
            summary = s.get("summary") or {}
            agentscope_agent_summary = [
                {
                    "name": "Policy-Agent",
                    "role": "제약 조건 강제 (ESS·거래·DR 검증 및 클램핑)",
                    "result": "제약 검증 완료",
                },
                {
                    "name": "SmartSeller-Agent",
                    "role": "잉여 에너지 판매 (bid_price, bid_quantity 결정)",
                    "result": summary.get("거래 권고", "—"),
                },
                {
                    "name": "StorageMaster-Agent",
                    "role": "ESS 운영 최적화 (charge/discharge/idle)",
                    "result": summary.get("ESS 스케줄", "—"),
                },
                {
                    "name": "EcoSaver-Agent",
                    "role": "수요반응 DR (demand response 권고)",
                    "result": summary.get("DR 이벤트", "—"),
                },
                {
                    "name": "MarketCoordinator-Agent",
                    "role": "협상 조율 및 충돌 해결, 최종 decisions 생성",
                    "result": (summary.get("결정 모드") or "—") + " · 최종 조율 완료",
                },
            ]
            break

    # Evaluation 탭(6): run별 평가 보고서 JSON 로드 (run_{id}_ 우선, 없으면 공통 evaluation_report.json)
    evaluation_report = None
    out_dir = PROJECT_ROOT / _output_dir()
    for candidate in (out_dir / f"run_{run_id}_evaluation_report.json", out_dir / "evaluation_report.json"):
        if candidate.is_file():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    evaluation_report = json.load(f)
                break
            except Exception as e:
                log.warning("evaluation_report read error path=%s: %s", candidate, e)

    # CDA 탭(4) 실행 서브탭: Order Book, Matching, Buyer, Settlement (run_{id}_cda_execution.json)
    cda_execution = None
    cda_exec_path = out_dir / f"run_{run_id}_cda_execution.json"
    if cda_exec_path.is_file():
        try:
            with open(cda_exec_path, "r", encoding="utf-8") as f:
                cda_execution = json.load(f)
        except Exception as e:
            log.warning("cda_execution read error path=%s: %s", cda_exec_path, e)

    prosumer_options = _prosumer_options(_db_path())
    search_run_date = (run.get("created_at") or "")[:10]
    search_prosumer = (run.get("args") or {}).get("prosumer") or ""
    search_measure_date = run.get("measure_date") or ""
    return render_template(
        "run_detail.html",
        run=run,
        current_tab=current_tab,
        current_sub=current_sub,
        current_sub4=current_sub4,
        current_path=request.path,
        prosumer_options=prosumer_options,
        search_run_date=search_run_date,
        search_prosumer=search_prosumer,
        search_measure_date=search_measure_date,
        alfp_agent_steps=alfp_agent_steps,
        alfp_domain_steps=alfp_domain_steps,
        cda_strategy_logs=cda_strategy_logs,
        cda_negotiation_logs=cda_negotiation_logs,
        agentscope_agent_summary=agentscope_agent_summary,
        agentscope_step3_stage=agentscope_step3_stage,
        evaluation_report=evaluation_report,
        cda_execution=cda_execution,
        **tabs,
    )


if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "0.0.0.0")  # 0.0.0.0: 브라우저/다른 기기에서 접근 가능
    port = int(os.environ.get("FLASK_PORT", "5001"))  # 기본 5001 (macOS에서 5000은 AirPlay가 사용)
    db_dir = os.environ.get("PIPELINE_DB_DIR", "output")
    log.info("Pipeline Dashboard starting  host=%s  port=%s  PIPELINE_DB_DIR=%s", host, port, db_dir)
    log.info("Log file: %s", LOG_DIR / f"dashboard_{datetime.now().strftime('%Y%m%d')}.log")
    log.info("Open http://127.0.0.1:%s in browser (or http://<this-machine-ip>:%s)", port, port)
    app.run(host=host, port=port, debug=True)
