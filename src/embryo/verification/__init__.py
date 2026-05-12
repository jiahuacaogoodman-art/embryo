"""Verification 模块 - 操作结果验证与失败分类

核心组件：
- verifier: 规则化验证器，根据 VerificationRule 判断步骤成功/失败
- failure_classifier: 失败类型分类，供 replan 使用精准错误信息
"""

from .verifier import Verifier, VerifyResult, VerifyStatus
from .failure_classifier import FailureClassifier, FailureType, ClassifiedFailure

__all__ = [
    "Verifier",
    "VerifyResult",
    "VerifyStatus",
    "FailureClassifier",
    "FailureType",
    "ClassifiedFailure",
]
