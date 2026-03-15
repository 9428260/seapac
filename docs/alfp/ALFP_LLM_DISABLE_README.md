# LLM 연계 일시 비활성화

ALFP·CDA·Governance 등에서 사용하는 LLM(Azure OpenAI) 호출을 **일시적으로 끄고** 규칙 기반 폴백만 쓰고 싶을 때 사용합니다.

## 설정 방법

환경변수 **`ALFP_DISABLE_LLM`** 을 다음 값 중 하나로 설정합니다.

- `1`
- `true`
- `yes`
- `on`

### 예시

```bash
# 셸에서 한 번만 비활성화
export ALFP_DISABLE_LLM=1
python -m run_full_pipeline --data-path data/test_2026may_seoul.pkl

# .env 파일에 추가 (프로젝트 루트)
ALFP_DISABLE_LLM=1
```

## 동작

- `ALFP_DISABLE_LLM` 이 설정되면 `get_llm()` 이 실제 API 대신 **스텁**을 반환합니다.
- 스텁의 `invoke()` 는 호출 시 **예외를 발생**시키며, 각 에이전트의 기존 `try/except` 에 의해 **규칙 기반 폴백**이 사용됩니다.
- LLM 입출력 로그(`logs/llm_io_*.log`)는 실제 호출이 없으므로 기록되지 않습니다.

## 다시 활성화

- 환경변수를 제거하거나 `0` / `false` 로 바꾼 뒤 **프로세스를 재시작**하면 LLM 연계가 다시 사용됩니다.

## 코드에서 확인

```python
from alfp.llm import is_llm_disabled, get_llm

if is_llm_disabled():
    # 규칙 기반만 사용하는 분기
    ...
else:
    llm = get_llm(temperature=0.0)
    ...
```
