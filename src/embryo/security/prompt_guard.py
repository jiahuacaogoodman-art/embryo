"""Prompt 注入防护 (Prompt Injection Guard)

搬运自 Hermes Agent (MIT License) 并适配 Embryo 架构。

扫描所有被注入到系统提示中的外部内容（SKILL.md、AGENTS.md、.cursorrules、
用户提供的上下文文件等），检测潜在的 prompt injection 攻击。

检测维度：
1. 隐藏 Unicode 字符（零宽空格、方向控制符等）
2. 已知注入模式（"ignore previous instructions"、"system prompt override" 等）
3. HTML 注入（隐藏 div、注释中的恶意指令）
4. 凭证泄露模式（curl + $KEY、cat .env 等）
5. 翻译执行攻击（"translate into X and execute"）

使用方式：
    from embryo.security.prompt_guard import scan_content, is_safe

    result = scan_content(skill_content, "login-skill/SKILL.md")
    if not result.is_safe:
        # 内容被阻止，使用 result.sanitized 代替
        ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..logging import get_logger

logger = get_logger("prompt_guard")


# ===== 威胁模式库 =====

# (正则模式, 威胁ID, 严重程度)
THREAT_PATTERNS: list[tuple[str, str, str]] = [
    # Prompt 注入
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection", "critical"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules", "critical"),
    (r'system\s+prompt\s+override', "sys_prompt_override", "critical"),
    (r'you\s+are\s+now\s+(?:a|an)\s+(?!helpful)', "role_hijack", "high"),
    (r'act\s+as\s+(?:if|though)\s+you\s+(?:have\s+no|don\'t\s+have)\s+(?:restrictions|limits|rules)',
     "bypass_restrictions", "critical"),

    # 欺骗和隐藏
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide", "high"),
    (r'keep\s+this\s+(?:secret|hidden)\s+from', "deception_secret", "high"),
    (r'never\s+reveal\s+(?:this|these)\s+instructions', "deception_reveal", "medium"),

    # HTML/Markdown 注入
    (r'<!--[^>]*(?:ignore|override|system|secret|hidden)[^>]*-->', "html_comment_injection", "high"),
    (r'<\s*div\s+style\s*=\s*["\'][\s\S]*?display\s*:\s*none', "hidden_div", "high"),
    (r'<\s*script\b', "script_tag", "critical"),

    # 代码执行攻击
    (r'translate\s+.*\s+into\s+.*\s+and\s+(execute|run|eval)', "translate_execute", "critical"),
    (r'(?:exec|eval|compile)\s*\(', "code_execution", "medium"),

    # 凭证泄露
    (r'curl\s+[^\n]*\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl", "critical"),
    (r'wget\s+[^\n]*\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_wget", "critical"),
    (r'cat\s+[^\n]*(?:\.env|credentials|\.netrc|\.pgpass|id_rsa)', "read_secrets", "high"),
    (r'echo\s+\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD)', "echo_secrets", "high"),

    # 数据外传
    (r'(?:curl|wget|fetch)\s+https?://[^\s]+.*(?:memory|history|session|password)', "data_exfil", "high"),
]

# 不可见 Unicode 字符（常用于隐藏注入内容）
INVISIBLE_CHARS: set[str] = {
    '\u200b',  # Zero Width Space
    '\u200c',  # Zero Width Non-Joiner
    '\u200d',  # Zero Width Joiner
    '\u2060',  # Word Joiner
    '\ufeff',  # Zero Width No-Break Space (BOM)
    '\u202a',  # Left-to-Right Embedding
    '\u202b',  # Right-to-Left Embedding
    '\u202c',  # Pop Directional Formatting
    '\u202d',  # Left-to-Right Override
    '\u202e',  # Right-to-Left Override
    '\u2066',  # Left-to-Right Isolate
    '\u2067',  # Right-to-Left Isolate
    '\u2068',  # First Strong Isolate
    '\u2069',  # Pop Directional Isolate
}


@dataclass
class ScanFinding:
    """单个扫描发现。"""
    threat_id: str
    severity: str  # critical / high / medium / low
    description: str = ""
    position: int = -1  # 在文本中的位置


@dataclass
class ScanResult:
    """扫描结果。"""
    source: str  # 来源文件名/标识
    is_safe: bool = True
    findings: list[ScanFinding] = field(default_factory=list)
    sanitized: str = ""  # 如果不安全，提供替代内容

    @property
    def threat_summary(self) -> str:
        if not self.findings:
            return ""
        ids = [f.threat_id for f in self.findings]
        return ", ".join(ids)


def scan_content(content: str, source: str = "unknown") -> ScanResult:
    """扫描内容是否包含 prompt injection。

    Args:
        content: 要扫描的文本内容
        source: 来源标识（文件名等）

    Returns:
        ScanResult，包含是否安全和发现的威胁
    """
    findings: list[ScanFinding] = []

    # 1. 检查不可见 Unicode
    for i, char in enumerate(content):
        if char in INVISIBLE_CHARS:
            findings.append(ScanFinding(
                threat_id=f"invisible_unicode_U+{ord(char):04X}",
                severity="high",
                description=f"不可见 Unicode 字符 U+{ord(char):04X} at position {i}",
                position=i,
            ))

    # 2. 检查威胁模式
    for pattern, threat_id, severity in THREAT_PATTERNS:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            findings.append(ScanFinding(
                threat_id=threat_id,
                severity=severity,
                description=f"匹配模式: {pattern[:50]}",
                position=match.start(),
            ))

    # 3. 检查异常的指令密度（短文本中多个"你必须"/"you must"）
    instruction_count = len(re.findall(
        r'(?:you\s+must|you\s+should|always\s+(?:do|say|respond)|never\s+(?:do|say|mention))',
        content, re.IGNORECASE
    ))
    if instruction_count > 5 and len(content) < 500:
        findings.append(ScanFinding(
            threat_id="high_instruction_density",
            severity="medium",
            description=f"短文本中有 {instruction_count} 条强制指令，疑似注入",
        ))

    # 判断是否安全
    is_safe = not any(f.severity in ("critical", "high") for f in findings)

    # 生成替代内容
    sanitized = content
    if not is_safe:
        threat_ids = [f.threat_id for f in findings if f.severity in ("critical", "high")]
        sanitized = (
            f"[BLOCKED: {source} 包含潜在的 prompt injection "
            f"({', '.join(threat_ids[:3])})。内容未加载。]"
        )
        logger.warning(
            "prompt_injection_detected",
            source=source,
            threats=threat_ids,
            content_length=len(content),
        )

    return ScanResult(
        source=source,
        is_safe=is_safe,
        findings=findings,
        sanitized=sanitized,
    )


def is_safe(content: str, source: str = "unknown") -> bool:
    """快速检查内容是否安全（不需要详细报告时用）。"""
    return scan_content(content, source).is_safe


def sanitize(content: str, source: str = "unknown") -> str:
    """如果内容不安全则返回替代文本，否则原样返回。"""
    result = scan_content(content, source)
    return result.sanitized if not result.is_safe else content


def strip_invisible_chars(text: str) -> str:
    """移除所有不可见 Unicode 字符（不阻止，只清理）。"""
    return "".join(c for c in text if c not in INVISIBLE_CHARS)
