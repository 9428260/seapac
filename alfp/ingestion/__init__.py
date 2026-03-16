"""
외부 ingest 모듈.

실시간/준실시간 측정값을 기존 dataset(timeseries)에 merge하는 보조 기능을 제공한다.
"""

from alfp.ingestion.live_data import (
    load_external_measurements,
    apply_external_measurements,
)

__all__ = [
    "load_external_measurements",
    "apply_external_measurements",
]
