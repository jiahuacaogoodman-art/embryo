"""Skill Validator - 质量门控

Skill 不能"成功一次就自动固化"。必须经过质量验证流程：

1. 成功 session → 生成 skill draft（status=draft）
2. dry-run replay 验证
3. 至少 N 次通过
4. 人工确认（可选）
5. 进入正式 skill registry（status=verified）

Skill 元数据包含：
- status: draft / testing / verified / deprecated
- success_count / failure_count
- last_verified_at
- required_backend
- risk_level
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..logging import get_logger

logger = get_logger(__name__)


class SkillStatus(str, Enum):
    """Skill 状态"""

    DRAFT = "draft"  # 刚生成，未验证
    TESTING = "testing"  # 正在验证中（dry-run）
    VERIFIED = "verified"  # 验证通过，可正式使用
    DEPRECATED = "deprecated"  # 已弃用
    FAILED = "failed"  # 验证失败


class RiskLevel(str, Enum):
    """Skill 风险等级"""

    LOW = "low"  # 只读操作（截图、OCR）
    MEDIUM = "medium"  # 写操作（点击、输入）
    HIGH = "high"  # 涉及表单提交、支付、删除等
    CRITICAL = "critical"  # 涉及系统命令、文件删除等


class SkillValidationMeta(BaseModel):
    """Skill 验证元数据

    附加在每个 Skill 上，跟踪其质量状态。
    """

    name: str
    version: str = "0.1.0"
    status: SkillStatus = SkillStatus.DRAFT
    risk_level: RiskLevel = RiskLevel.MEDIUM

    # 验证统计
    success_count: int = 0
    failure_count: int = 0
    total_runs: int = 0
    last_verified_at: Optional[float] = None
    last_failed_at: Optional[float] = None

    # 要求
    required_backend: list[str] = Field(default_factory=list)  # e.g. ["playwright", "pyautogui"]
    min_passes_for_verification: int = 3  # 至少通过 N 次才能 verified
    require_human_approval: bool = False  # 是否需要人工确认

    # 来源
    created_at: float = Field(default_factory=time.time)
    created_from: str = ""  # session ID / manual / imported
    approved_by: str = ""  # 人工确认者

    # 额外信息
    failure_reasons: list[str] = Field(default_factory=list)  # 最近的失败原因
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """成功率"""
        if self.total_runs == 0:
            return 0.0
        return self.success_count / self.total_runs

    @property
    def is_usable(self) -> bool:
        """是否可以被使用（verified 或正在 testing 的也允许使用）"""
        return self.status in (SkillStatus.VERIFIED, SkillStatus.TESTING)

    @property
    def needs_more_validation(self) -> bool:
        """是否还需要更多验证"""
        if self.status == SkillStatus.VERIFIED:
            return False
        return self.success_count < self.min_passes_for_verification

    def to_yaml_block(self) -> str:
        """生成 YAML frontmatter 块（嵌入到 SKILL.md）"""
        lines = [
            f"status: {self.status.value}",
            f"version: {self.version}",
            f"risk_level: {self.risk_level.value}",
            f"success_count: {self.success_count}",
            f"failure_count: {self.failure_count}",
            f"total_runs: {self.total_runs}",
        ]
        if self.last_verified_at:
            lines.append(
                f"last_verified_at: {time.strftime('%Y-%m-%d', time.localtime(self.last_verified_at))}"
            )
        if self.required_backend:
            lines.append(f"required_backend: [{', '.join(self.required_backend)}]")
        return "\n".join(lines)


class SkillValidator:
    """Skill 质量门控验证器

    管理 Skill 从 draft → testing → verified 的生命周期。

    用法：
        validator = SkillValidator()
        meta = validator.get_or_create_meta("login-demo")
        validator.record_run(meta, success=True)
        if validator.check_promotion(meta):
            # skill 已升级为 verified
    """

    def __init__(
        self,
        min_passes: int = 3,
        max_failure_rate: float = 0.3,
        require_human_approval: bool = False,
    ):
        """
        Args:
            min_passes: 最少通过次数才能 verified
            max_failure_rate: 最大允许失败率
            require_human_approval: 是否需要人工确认
        """
        self._min_passes = min_passes
        self._max_failure_rate = max_failure_rate
        self._require_human_approval = require_human_approval
        self._metas: dict[str, SkillValidationMeta] = {}

    def get_or_create_meta(self, skill_name: str) -> SkillValidationMeta:
        """获取或创建 Skill 验证元数据"""
        if skill_name not in self._metas:
            self._metas[skill_name] = SkillValidationMeta(
                name=skill_name,
                min_passes_for_verification=self._min_passes,
                require_human_approval=self._require_human_approval,
            )
        return self._metas[skill_name]

    def record_run(
        self,
        meta: SkillValidationMeta,
        success: bool,
        failure_reason: str = "",
    ) -> None:
        """记录一次 Skill 执行结果

        Args:
            meta: Skill 验证元数据
            success: 是否成功
            failure_reason: 失败原因（仅失败时）
        """
        meta.total_runs += 1

        if success:
            meta.success_count += 1
            meta.last_verified_at = time.time()
            logger.info(
                "skill_run_success",
                skill=meta.name,
                count=meta.success_count,
                total=meta.total_runs,
            )
        else:
            meta.failure_count += 1
            meta.last_failed_at = time.time()
            if failure_reason:
                meta.failure_reasons.append(failure_reason)
                # 只保留最近 10 条
                meta.failure_reasons = meta.failure_reasons[-10:]
            logger.warning(
                "skill_run_failed",
                skill=meta.name,
                reason=failure_reason[:100],
                total=meta.total_runs,
            )

        # 如果还是 draft，升级为 testing
        if meta.status == SkillStatus.DRAFT and meta.total_runs >= 1:
            meta.status = SkillStatus.TESTING
            logger.info("skill_status_changed", skill=meta.name, new_status="testing")

        # 检查是否应该升级或降级
        self.check_promotion(meta)
        self.check_demotion(meta)

    def check_promotion(self, meta: SkillValidationMeta) -> bool:
        """检查是否可以升级为 verified

        条件：
        1. 至少 min_passes 次成功
        2. 失败率不超过 max_failure_rate
        3. 如果 require_human_approval，需要 approved_by 非空

        Returns:
            是否升级成功
        """
        if meta.status == SkillStatus.VERIFIED:
            return True  # 已经是 verified

        if meta.success_count < meta.min_passes_for_verification:
            return False

        if meta.total_runs > 0 and meta.failure_count / meta.total_runs > self._max_failure_rate:
            return False

        if meta.require_human_approval and not meta.approved_by:
            return False

        # 升级
        meta.status = SkillStatus.VERIFIED
        meta.last_verified_at = time.time()
        logger.info(
            "skill_promoted_to_verified",
            skill=meta.name,
            success_count=meta.success_count,
            total_runs=meta.total_runs,
        )
        return True

    def check_demotion(self, meta: SkillValidationMeta) -> bool:
        """检查是否需要降级

        条件：
        - 连续失败超过 3 次
        - 或失败率超过 50%（且有足够样本）

        Returns:
            是否降级了
        """
        if meta.status == SkillStatus.DRAFT:
            return False

        # 连续失败检测：最近 3 次都失败
        if meta.failure_count >= 3 and meta.success_count == 0:
            meta.status = SkillStatus.FAILED
            logger.warning("skill_demoted_to_failed", skill=meta.name)
            return True

        # 高失败率（至少 5 次运行后判断）
        if meta.total_runs >= 5 and meta.failure_count / meta.total_runs > 0.5:
            if meta.status == SkillStatus.VERIFIED:
                meta.status = SkillStatus.TESTING  # 从 verified 降到 testing
                logger.warning("skill_demoted_to_testing", skill=meta.name)
                return True

        return False

    def approve(self, skill_name: str, approver: str) -> bool:
        """人工确认 Skill

        Args:
            skill_name: Skill 名称
            approver: 确认者标识

        Returns:
            是否确认成功
        """
        meta = self.get_or_create_meta(skill_name)
        meta.approved_by = approver
        logger.info("skill_approved", skill=skill_name, by=approver)

        # 确认后检查是否可以升级
        return self.check_promotion(meta)

    def can_use_skill(self, skill_name: str) -> tuple[bool, str]:
        """检查 Skill 是否可以使用

        Returns:
            (can_use, reason)
        """
        meta = self.get_or_create_meta(skill_name)

        if meta.status == SkillStatus.DEPRECATED:
            return False, "Skill 已弃用"

        if meta.status == SkillStatus.FAILED:
            return False, f"Skill 验证失败（{meta.failure_count} 次失败）"

        if meta.status == SkillStatus.DRAFT:
            return True, "Skill 尚未验证（draft），使用风险较高"

        if meta.status == SkillStatus.TESTING:
            return True, f"Skill 正在验证中（{meta.success_count}/{meta.min_passes_for_verification} 次通过）"

        return True, "Skill 已验证通过"

    def get_all_metas(self) -> list[SkillValidationMeta]:
        """获取所有 Skill 的验证元数据"""
        return list(self._metas.values())

    def get_summary(self) -> dict[str, Any]:
        """获取验证系统摘要"""
        metas = list(self._metas.values())
        return {
            "total": len(metas),
            "draft": sum(1 for m in metas if m.status == SkillStatus.DRAFT),
            "testing": sum(1 for m in metas if m.status == SkillStatus.TESTING),
            "verified": sum(1 for m in metas if m.status == SkillStatus.VERIFIED),
            "failed": sum(1 for m in metas if m.status == SkillStatus.FAILED),
            "deprecated": sum(1 for m in metas if m.status == SkillStatus.DEPRECATED),
        }
