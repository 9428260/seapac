# Continuous Double Auction 기반 Multi-Agent Energy Market

## Product Requirement Document (PRD)

------------------------------------------------------------------------

# 1. Product Overview

## 1.1 목적

본 시스템은 에너지 커뮤니티 내 분산 전력 자원(DER)을 효율적으로 거래하기
위해 Continuous Double Auction (CDA) 기반 전력 시장을 구축한다.

참여 에이전트 - Prosumer (Seller Agent) - Consumer (Buyer Agent) -
Storage Agent (ESS) - Market Coordinator - Policy / Trust Agent

본 시스템은 Mesa 기반 시뮬레이션 환경에서 에너지 수급을 반영하여 실시간
전력 거래를 수행한다.

------------------------------------------------------------------------

# 2. Problem Statement

현재 공동주택 에너지 환경 문제

  문제                      설명
  ------------------------- -------------------------------
  잉여전력 판매 단가 낮음   한전에 저가 판매
  단지 내 거래 부재         Prosumer ↔ Consumer 거래 없음
  ESS 활용 부족             피크 대응 미흡
  가격 발견 기능 없음       시장 메커니즘 부족

CDA 시장 도입 효과 - 가격 발견 (price discovery) - 수요 공급 자동 매칭 -
실시간 거래 - 효율적 자원 배분

------------------------------------------------------------------------

# 3. System Scope

  기능              설명
  ----------------- ---------------------
  Bid 생성          Seller Agent
  Ask 생성          Buyer Agent
  Order Book 관리   Market Coordinator
  거래 매칭         CDA Matching Engine
  정산              Settlement Engine
  정책 검증         Policy Agent
  시뮬레이션 연동   Mesa

------------------------------------------------------------------------

# 4. System Architecture

MESA Simulation Layer ↓ State Translator ↓ Multi-Agent System ↓ CDA
Market Engine ↓ Settlement Engine ↓ Simulation Update

------------------------------------------------------------------------

# 5. Market Mechanism

## Continuous Double Auction

판매자는 Ask 제출 구매자는 Bid 제출

거래 조건

Bid Price ≥ Ask Price

------------------------------------------------------------------------

## 거래 가격

Trade Price = (Bid Price + Ask Price) / 2

------------------------------------------------------------------------

# 6. Agent Specification

## Seller Agent (SmartSeller)

목표: 잉여 전력 판매 수익 극대화

입력 - Surplus Energy - Community Demand - Grid Price - Historical Trade
Price

출력 - Ask Price - Ask Quantity

------------------------------------------------------------------------

## Buyer Agent

목표: 필요한 전력을 최소 비용으로 구매

입력 - Energy Deficit - Market Price - Peak Risk

출력 - Bid Price - Bid Quantity

------------------------------------------------------------------------

## Storage Agent

ESS를 활용하여 시장 참여

전략 - 낮은 가격 충전 - 높은 가격 판매

------------------------------------------------------------------------

## Market Coordinator Agent

기능 - Order Book 관리 - 매칭 실행 - 거래 체결 - 시장 통계 생성

------------------------------------------------------------------------

## Policy Agent

검증 항목

  항목        설명
  ----------- ----------------
  가격 범위   최소/최대 가격
  거래량      공급량 검증
  ESS SOC     안전 범위

------------------------------------------------------------------------

# 7. Market Data Structures

## Bid Table

| Agent \| Price \| Quantity \|

## Ask Table

| Agent \| Price \| Quantity \|

------------------------------------------------------------------------

# 8. Matching Engine

Step 1: Bid 정렬 (가격 기준 내림차순)

Step 2: Ask 정렬 (가격 기준 오름차순)

Step 3: 매칭 조건

Highest Bid ≥ Lowest Ask

------------------------------------------------------------------------

# 9. Settlement Engine

기능 - 거래 기록 저장 - 에너지 잔고 업데이트 - ESS 상태 업데이트

------------------------------------------------------------------------

# 10. Integration with Mesa

Simulation step

1 Mesa 상태 업데이트 2 Agent 전략 생성 3 CDA 시장 실행 4 거래 결과 반영
5 ESS 및 Load 업데이트 6 KPI 계산

------------------------------------------------------------------------

# 11. KPI

  KPI                   설명
  --------------------- ----------------
  Trading Volume        거래량
  Market Liquidity      시장 유동성
  Average Trade Price   평균 거래 가격
  Peak Reduction        피크 감소
  Energy Cost Saving    비용 절감

------------------------------------------------------------------------

# 12. Non Functional Requirements

  항목       요구사항
  ---------- ----------------
  확장성     1000+ agent
  지연시간   \<1초
  신뢰성     거래 로그 보존
  투명성     거래 기록 감사

------------------------------------------------------------------------

# 13. Technology Stack

  Layer           Technology
  --------------- -----------------
  Simulation      Mesa
  Multi-Agent     AgentScope
  LLM             GPT / Local LLM
  Market Engine   Python
  API             FastAPI
  Database        TimescaleDB

------------------------------------------------------------------------

# 14. Future Enhancements

Phase 2 - Reinforcement Learning 전략 - Adaptive bidding

Phase 3 - Smart meter integration - Blockchain settlement

------------------------------------------------------------------------

# 15. Success Metrics

  Metric           Target
  ---------------- --------
  전력 거래 비율   30%
  피크 감소        15%
  비용 절감        10%
  ESS 활용률       70%
