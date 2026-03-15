# test_2026may_seoul.pkl 전력거래 분석

## 데이터 개요
- **파일**: `data/test_2026may_seoul.pkl`
- **기간**: 31일 (15분 스텝 2,976개)
- **프로슈머 수**: 20개
- **전력거래 조건**: 동일 스텝에서 **잉여(판매자)** 와 **부족(구매자)** 가 동시에 있어야 함  
  - `surplus_kw = max(pv_kw - load_kw, 0)` ≥ 0.2 kW → 판매자  
  - `deficit_kw = max(load_kw - pv_kw, 0)` ≥ 0.2 kW → 구매자  

---

## 전력거래가 이루어지는 프로슈머 ID

### 판매자 후보 (일부 스텝에서 잉여 발생)
| Prosumer ID | 역할 | 잉여 발생 스텝 수(거래가능 구간) | 비고 |
|-------------|------|----------------------------------|------|
| bus_74_EnergyHub | Seller | 16 | 잉여량 가장 많음 |
| bus_134_EnergyHub | Seller | 15 | |
| bus_116_EnergyHub | Seller | 14 | |
| bus_100_EnergyHub | Seller | 13 | |
| bus_109_Rural | Seller | 9 | |
| bus_140_Rural | Seller | 8 | |
| bus_130_Rural | Seller | 7 | |
| bus_59_Rural | Seller | 7 | |

### 구매자 후보 (대부분 스텝에서 부족)
- **Commercial**: bus_48_Commercial, bus_78_Commercial, bus_102_Commercial, bus_127_Commercial  
- **Industrial**: bus_67_Industrial, bus_95_Industrial, bus_133_Industrial, bus_136_Industrial  
- **Residential**: bus_62_Residential, bus_86_Residential, bus_106_Residential, bus_138_Residential  
- **Rural / EnergyHub**: 낮 시간대에만 일부 잉여, 나머지 시간에는 구매자로 참여  

---

## 테스트 추천 조합 (전력거래 발생용)

**여러 프로슈머를 선택할 때**, 판매자형과 구매자형을 함께 넣어야 거래가 발생합니다.

1. **최소 조합 (2~3명)**  
   - `bus_74_EnergyHub`, `bus_48_Commercial`  
   - 또는 `bus_134_EnergyHub`, `bus_78_Commercial`

2. **권장 조합 (판매 2 + 구매 2~3)**  
   - `bus_74_EnergyHub`, `bus_134_EnergyHub`, `bus_48_Commercial`, `bus_78_Commercial`  
   - 또는 `bus_116_EnergyHub`, `bus_100_EnergyHub`, `bus_67_Industrial`, `bus_62_Residential`

3. **EnergyHub 4개 + Commercial 2개 (거래 기회 최대)**  
   - `bus_74_EnergyHub`, `bus_134_EnergyHub`, `bus_116_EnergyHub`, `bus_100_EnergyHub`, `bus_48_Commercial`, `bus_78_Commercial`

---

## 참고 (데이터 기간과 거래 가능 스텝)
- **첫 1일(96스텝)**: 이 데이터에서는 첫날 구간에 “잉여 있는 프로슈머”가 없어 **거래 가능 스텝 0**입니다.
- **거래가 발생하는 96스텝 구간 예**: 2026-05-04 13:00 ~ 2026-05-05 12:45 구간에서는 **8스텝**에서 거래 가능합니다.

파이프라인이 **데이터의 첫 96스텝**만 사용하는 경우, 위 프로슈머 조합을 써도 당일에는 거래가 없을 수 있습니다.  
다른 일자(measure_date)를 쓰거나, 데이터 구간을 바꾸면 같은 조합으로도 전력거래가 발생합니다.
