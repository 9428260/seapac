"""
런 간·영구 메모리 (Persistent Memory).
프로슈머별로 이전 실행의 계획·검증·의사결정 결과를 저장/로드합니다.
"""

from alfp.memory.store import load_memory, save_memory

__all__ = ["load_memory", "save_memory"]
