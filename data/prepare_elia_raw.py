"""
ods132.csv를 ELIA 스키마 컬럼명으로 변환하여 elia_raw.csv 생성.
converter가 기대하는 컬럼: datetime, resolutioncode, afrrbeup, mfrrbesaup, mfrrbedaup,
  afrrbedown, mfrrbesadown, mfrrbedadown
"""
import pandas as pd
from pathlib import Path

ODS132_PATH = Path("ods132.csv")
ELIA_RAW_PATH = Path("elia_raw.csv")

# ods132 컬럼명 -> ELIA 스키마 컬럼명
RENAME = {
    "Datetime": "datetime",
    "Resolution code": "resolutioncode",
    "aFRR BE +": "afrrbeup",
    "mFRR BE SA +": "mfrrbesaup",
    "mFRR BE DA +": "mfrrbedaup",
    "aFRR BE -": "afrrbedown",
    "mFRR BE SA -": "mfrrbesadown",
    "mFRR BE DA -": "mfrrbedadown",
}

def main():
    df = pd.read_csv(ODS132_PATH, sep=";")
    df = df.rename(columns=RENAME)
    df.to_csv(ELIA_RAW_PATH, index=False)
    print(f"Saved {ELIA_RAW_PATH} with {len(df)} rows")
    print("Columns:", list(df.columns))

if __name__ == "__main__":
    main()
