"""
ALFP 설정 로더.
- skills_config.json: 스킬/에이전트 파라미터
- prompts/*.txt: LLM 프롬프트 (소스 수정 없이 변경 가능)
"""

from alfp.config.loader import get_skills_config
from alfp.config.prompt_loader import get_prompt, get_system_prompt, get_user_prompt_template

__all__ = [
    "get_skills_config",
    "get_prompt",
    "get_system_prompt",
    "get_user_prompt_template",
]
