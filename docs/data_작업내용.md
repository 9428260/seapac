# 데이터 폴더 작업 내용

이 문서는 `data` 폴더에서 수행한 작업 전반을 정리한 요약입니다.  
상세 절차·구조는 각 주제별 md 파일을 참고하면 됩니다.

---

## 1. 작업 목적

- **py 파일** (`elia_ieee141_reproduction_converter.py`)을 **pkl·csv**와 함께 실행해 **분석대상 데이터** 생성
- **ipynb** (`IEEE141_grid_example_notebook.ipynb`)를 참고해 py 실행 방법 파악 및 동일 흐름으로 데이터 생성
- 생성된 pkl을 **사람이 볼 수 있는 방법** 정리
- **실제 IEEE 141버스 계통 데이터**로 그리드·재현 데이터셋 재생성
- 위 과정을 **md 파일**로 문서화

---

## 2. 수행한 작업 요약

| 순서 | 작업 내용 | 결과물·참고 |
|------|-----------|-------------|
| 1 | py·ipynb 분석 | converter는 ELIA 원시 CSV + IEEE141 grid pkl → 재현 데이터셋 pkl 생성. 노트북은 해당 pkl 사용 예시. |
| 2 | ELIA 원시 CSV 준비 | `ods132.csv`(세미콜론·다른 컬럼명) → ELIA 스키마에 맞게 변환 스크립트 작성 → `elia_raw.csv` 생성 |
| 3 | IEEE141 그리드 pkl 준비 | (가) 최소 구조: `create_ieee141_grid_pkl.py` (20버스). (나) **실제 계통**: `build_ieee141_from_matpower.py` + `case141.m` (141버스·140브랜치·1발전기) |
| 4 | converter 실행 | `elia_ieee141_reproduction_converter.py` 실행 → `paper_reproduction_dataset_from_screenshot_schema.pkl` 생성. 타임존·resample 오류 수정 반영. |
| 5 | pkl 보는 방법 정리 | `inspect_pkl.py` 추가, `README_pkl_view.md` 작성 (요약 출력·CSV 내보내기·Python/노트북 사용법) |
| 6 | 실제 IEEE 141버스로 재생성 | MATPOWER case141 파싱 스크립트 작성, `case141.m` 다운로드, grid pkl·재현 데이터셋 pkl 재생성 |
| 7 | 작성 과정 문서화 | `paper_reproduction_dataset_creation.md` – pkl 생성 과정·입출력·재현 방법 상세 기술 |
| 8 | 작업 내용 md 저장 | 본 문서 `data_작업내용.md` – 전체 작업 요약·파일 목록·실행 순서 정리 |

---

## 3. 생성·수정된 파일 목록

### 3.1 새로 만든 파일

| 파일 | 설명 |
|------|------|
| `prepare_elia_raw.py` | ods132.csv → elia_raw.csv (ELIA 스키마 컬럼명·구분자 변환) |
| `create_ieee141_grid_pkl.py` | 최소 IEEE141 그리드 pkl 생성 (20버스, 프로슈머용) |
| `build_ieee141_from_matpower.py` | case141.m 파싱 → 실제 141버스 그리드 pkl 생성 |
| `inspect_pkl.py` | pkl 구조·앞부분 출력, 선택 시 CSV 내보내기 |
| `elia_raw.csv` | ods132를 ELIA 스키마로 변환한 CSV (converter 입력) |
| `case141.m` | MATPOWER IEEE 141버스 계통 데이터 (다운로드본) |
| `IEEE141_grid.pkl` | 그리드 pkl (현재는 build_ieee141_from_matpower.py로 생성한 실제 계통) |
| `paper_reproduction_dataset_from_screenshot_schema.pkl` | 최종 재현 데이터셋 pkl (분석대상 데이터) |
| `README_pkl_view.md` | pkl을 사람이 보는 방법 (스크립트·CSV·Python·노트북) |
| `paper_reproduction_dataset_creation.md` | 재현 pkl 작성 과정 상세 (입력·단계·converter 흐름·재현 방법) |
| `data_작업내용.md` | 본 문서 – 전체 작업 내용 요약 |
| `build_train_test_datasets.py` | 학습용(2026년)·테스트용(2026년 5월 1개월) 데이터셋 생성, 서울 시간대, 프로슈머 ID `bus_{id}_{type}` |
| `train_2026_seoul.pkl` | 학습용 데이터셋 (2026년, Asia/Seoul, 20개 프로슈머 시계열) |
| `test_2026may_seoul.pkl` | 테스트용 데이터셋 (2026년 5월 1개월, 학습 이후 평가용) |

### 3.2 수정한 파일

| 파일 | 수정 내용 |
|------|-----------|
| `elia_ieee141_reproduction_converter.py` | `pd.to_datetime(..., utc=True)` 적용(타임존 통일), resample 전 `resolutioncode` 제거, `target_tz`(서울 등), `prosumer_id`(bus_{id}_{type}), `build_dataset_from_elia_df` 추가 |

### 3.3 기존 파일 (참고·입력용)

| 파일 | 용도 |
|------|------|
| `ods132.csv` | ELIA 형식 원시 데이터 (prepare_elia_raw.py 입력) |
| `elia_ieee141_reproduction_converter.py` | 재현 데이터셋 생성 메인 스크립트 |
| `IEEE141_grid_example_notebook.ipynb` | IEEE141 pkl 사용 예시·실행 방법 참고 |

---

## 4. 데이터 재현 방법 (실행 순서)

실제 IEEE 141버스 계통으로 분석대상 데이터를 다시 만들 때:

```bash
cd /Users/a09206/work/ai_master_26/data

# 1) ods132 → elia_raw.csv
python3 prepare_elia_raw.py

# 2) case141.m → IEEE141_grid.pkl (case141.m 없으면 아래로 다운로드)
# curl -sL "https://raw.githubusercontent.com/MATPOWER/matpower/master/data/case141.m" -o case141.m
python3 build_ieee141_from_matpower.py

# 3) 재현 데이터셋 pkl 생성
python3 elia_ieee141_reproduction_converter.py
```

의존성: `pandas`, `numpy` (`pip install pandas numpy`).

**학습·테스트 데이터셋(서울 시간대, 프로슈머 ID 적용) 생성:**

```bash
cd data
# 위 1~2 단계로 elia_raw.csv, IEEE141_grid.pkl 준비 후
python3 build_train_test_datasets.py
```

- **train_2026_seoul.pkl**: 2026년 데이터, 시간대 Asia/Seoul, `timeseries.prosumer_id` = `bus_{id}_{prosumer_type}` (20개 구분)
- **test_2026may_seoul.pkl**: 2026년 5월 1개월, 동일 시간대·동일 프로슈머 ID, 학습 이후 테스트용

---

## 5. 관련 문서 참조

| 문서 | 내용 |
|------|------|
| `paper_reproduction_dataset_creation.md` | paper_reproduction_dataset_from_screenshot_schema.pkl 작성 과정 상세 (입력·단계·converter 흐름·수정 사항·재현 방법) |
| `README_pkl_view.md` | pkl을 사람이 보는 방법 (inspect_pkl.py, CSV 내보내기, Python/노트북 예시) |

---

## 6. 출력 데이터 개요

- **paper_reproduction_dataset_from_screenshot_schema.pkl**
  - `metadata`, `elia_raw`, `elia_internal`, `grid`(buses 141, branches 140, generators 1), `prosumers`(20), `timeseries`(약 125만 행, 15분 해상도)
- **grid**: MATPOWER case141 기반 실제 IEEE 141버스 배전계통(Khodr et al., Caracas).
- **train_2026_seoul.pkl** / **test_2026may_seoul.pkl**: 학습용(2026년)·테스트용(2026년 5월 1개월), 시간대 Asia/Seoul, 프로슈머 20개 식별자 `prosumer_id` = `bus_48_Commercial`, `bus_62_Residential` 등.

이 파일들을 기준으로 분석·시각화·강화학습 환경 등 downstream 작업을 이어가면 됩니다.
