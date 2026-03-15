# 문서 디렉터리 구조

성격별로 분류한 문서 디렉터리입니다.

| 디렉터리 | 성격 | 주요 문서 예시 |
|----------|------|----------------|
| **prd/** | 요구사항 정의서 (Product Requirements Document) | seapac_agentic_prd.md, cda_energy_market_prd.md, seapac_parallel_agents_prd.md |
| **architecture/** | 시스템·파이프라인 아키텍처, 검증, 구현 현황 | DEEPAGENT_DOMAIN_ARCHITECTURE.md, PIPELINE_STEPS_VERIFICATION.md, PRD_IMPLEMENTATION_STATUS.md |
| **alfp/** | ALFP 에이전트, 메모리, 설정, 스킬, 도구, 로깅 | alfp_memory_README.md, alfp_config_README_SKILLS_CONFIG.md, ALFP_AGENT_STEP_LOGGING.md |
| **simulation/** | Mesa 시뮬레이션, 실행 단계 | MESA_SIMULATION_MEANING_IN_SYSTEM.md, simulation_README_EXECUTION.md |
| **modules/** | 모듈별 README (seapac, cda, dashboard, parallel_agents) | seapac_agents_README_RUN_EXECUTION.md, cda_README.md, pipeline_dashboard_README.md |
| **data/** | 데이터셋·데이터 작업 | data_README_test_2026may_seoul.md, data_작업내용.md |
| **guides/** | 사용 가이드 | SKILLS_AND_DATA_GUIDE.md |

문서 간 링크는 상대 경로(예: `../prd/...`, `../modules/...`)로 되어 있으므로 디렉터리 구조를 유지한 채로 이동해도 동작합니다.
