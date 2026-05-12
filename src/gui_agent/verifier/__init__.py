"""结果验证模块

每步操作后重新采集界面状态，判断操作是否达到预期结果。
"""

from .result_verifier import ResultVerifier

__all__ = ["ResultVerifier"]
