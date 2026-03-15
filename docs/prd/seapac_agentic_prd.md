# PRD: Agentic Decision Layer for MESA Energy Simulation

## Overview

This document defines the Product Requirement for implementing Steps
2--5 of an Agentic Decision Layer on top of a MESA-based energy
community simulation system (SEAPAC).

The system integrates: - Mesa simulation environment - LLM-based
multi-agent orchestration (AgentScope) - Energy market coordination -
ESS control - Demand-side energy savings

------------------------------------------------------------------------

# Step 2 --- State Translator

## Purpose

Transform raw simulation state data from Mesa into structured summaries
suitable for LLM agents.

## Responsibilities

-   Extract relevant features from Mesa DataCollector
-   Compress high dimensional data
-   Produce LLM-friendly JSON
-   Generate human-readable summary

## Example JSON Output

``` json
{
 "time": "18:00",
 "community_state": {
  "total_load": 520,
  "pv_generation": 240,
  "surplus_energy": 60,
  "deficit_energy": 340,
  "peak_risk": "HIGH"
 },
 "market_state": {
  "grid_price": 120,
  "community_trade_price_range": [80,110]
 },
 "ess_state": {
  "soc": 65,
  "capacity": 500,
  "available_discharge": 175
 }
}
```

------------------------------------------------------------------------

# Step 3 --- Multi-Agent Decision Engine

Agents coordinate decisions for energy trading, storage control and
consumption reduction.

## Agents

### SmartSeller-Agent

Goal: maximize revenue from surplus energy

Outputs - bid_price - bid_quantity

### StorageMaster-Agent

Goal: optimize ESS operation

Outputs - charge/discharge/idle - power level

### EcoSaver-Agent

Goal: reduce energy consumption

Outputs - demand response recommendations

### MarketCoordinator-Agent

Goal: coordinate negotiation and resolve conflicts

### Policy-Agent

Goal: enforce constraints

------------------------------------------------------------------------

# Step 4 --- Action Execution Engine

Responsible for applying validated agent decisions to the Mesa
simulation.

## Execution Flow

Agent Proposal → Policy Validation → Coordinator Approval → Mesa Update

Supported actions: - TradeAction - ESSAction - DemandResponseAction

## Implementation (실행 단계 구현)

- **Module**: `seapac_agents/execution.py` — Action types, policy validation, `run_execution(decisions, ...)` → Mesa update.
- **CLI**: `simulation/run_execution.py` — Run execution stage with `--use-alfp` (decisions from ALFP) or `--decisions-file` (JSON). Optionally `--output-dir` and `--save-csv` for Step 5 Evaluation input.
- **Doc**: [seapac_agents_README_RUN_EXECUTION.md](../modules/seapac_agents_README_RUN_EXECUTION.md) — Usage and pipeline position.

------------------------------------------------------------------------

# Step 5 --- Evaluation Engine

Evaluates system performance after each simulation step.

## KPIs

  Metric                 Description
  ---------------------- --------------------------------------
  Energy Cost            Total grid purchase cost
  Trading Profit         Profit from community energy trading
  Peak Reduction         Reduction in peak demand
  ESS Degradation Cost   Battery wear
  User Acceptance        Acceptance rate of recommendations

------------------------------------------------------------------------

# System Architecture

MESA Simulation → State Translator → Multi-Agent Decision → Action
Execution → Evaluation

------------------------------------------------------------------------

# Technology Stack

Simulation: Mesa\
Agents: AgentScope\
LLM: GPT / Local LLM\
API: FastAPI\
Storage: TimescaleDB
