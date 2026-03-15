"""
Flask UI for SEAPAC pipeline runs and stage results.

Run:
  export PIPELINE_DB_DIR=output   # optional, default: output
  python -m pipeline_dashboard.app
  # or: flask --app pipeline_dashboard.app run
  # Open http://127.0.0.1:5001 (default port 5001; macOS uses 5000 for AirPlay)
"""

from __future__ import annotations

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


@app.route("/api/run", methods=["POST"])
def api_run():
    """
    Request body (JSON): data_path, steps, prosumer, use_parallel, phase, output_dir(optional).
    Creates a run in DB, starts pipeline subprocess with PIPELINE_RUN_ID, returns run_id.
    """
    out_dir = _output_dir()
    data = request.get_json(force=True, silent=True) or {}
    data_path = data.get("data_path", "data/test_2026may_seoul.pkl")
    steps = int(data.get("steps", 96))
    prosumer = data.get("prosumer", "bus_48_Commercial")
    use_parallel = bool(data.get("use_parallel", False))
    phase = int(data.get("phase", 4))
    skip_alfp = bool(data.get("skip_alfp", False))
    measure_date = (data.get("measure_date") or "").strip() or None
    run_date = (data.get("run_date") or "").strip() or None
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if not run_date:
        run_date = today
    # Measure date: from data file first (period_start / first timestamp), else form, else today
    measure_date_from_data = _measure_date_from_data(data_path)
    if measure_date_from_data:
        measure_date = measure_date_from_data
    elif not measure_date:
        measure_date = today

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
    init_db(_db_path())
    run_id = create_run(args_dict, db_path=_db_path())

    # subprocess가 Dashboard와 동일한 DB 파일에 기록하도록 절대 경로로 전달
    out_dir_abs = str(PROJECT_ROOT / out_dir)

    cmd = [
        sys.executable,
        "-m",
        "run_full_pipeline",
        "--data-path", data_path,
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
    # subprocess에서 pipeline_dashboard 등 프로젝트 모듈을 찾을 수 있도록
    prev_pp = env.get("PYTHONPATH", "")
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


@app.route("/runs/<int:run_id>")
def run_detail(run_id: int):
    """Show one run and its stages (architecture step results). Langchain DeepAgent 단계는 탭1에서 조회."""
    init_db(_db_path())
    run = get_run_with_stages(run_id, db_path=_db_path())
    if run is None:
        abort(404)
    stages = run.get("stages", [])
    tabs = _stages_for_tabs(stages)
    # ALFP(Stage 1) 실행 시 DB에 기록된 Agent별 단계 로그 조회 (docs/ALFP_AGENT_STEP_LOGGING.md)
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
    prosumer_options = _prosumer_options(_db_path())
    search_run_date = (run.get("created_at") or "")[:10]
    search_prosumer = (run.get("args") or {}).get("prosumer") or ""
    search_measure_date = run.get("measure_date") or ""
    return render_template(
        "run_detail.html",
        run=run,
        current_tab=current_tab,
        current_sub=current_sub,
        current_path=request.path,
        prosumer_options=prosumer_options,
        search_run_date=search_run_date,
        search_prosumer=search_prosumer,
        search_measure_date=search_measure_date,
        alfp_agent_steps=alfp_agent_steps,
        alfp_domain_steps=alfp_domain_steps,
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
