"""隐私引擎：敏感检测 + 可逆/不可逆脱敏 + 响应还原 + 会话粘性（零依赖）。"""

from __future__ import annotations

import base64
import binascii
import json
import re
import threading
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import PrivacyConfig


@dataclass
class PatternRule:
    name: str
    pattern: re.Pattern[str]
    category: str
    irreversible: bool = False   # True = 掩码/删除，响应不回填
    block: bool = False          # True = 命中直接阻断（无论模式）
    safe: bool = True            # True = 命中触发敏感路由


@dataclass
class Match:
    rule: PatternRule
    start: int
    end: int
    value: str


@dataclass
class ScanResult:
    sensitive: bool = False
    categories: set[str] = field(default_factory=set)
    blocked: bool = False
    encoded_bypass: bool = False
    matches: list[Match] = field(default_factory=list)


# 内置规则（正则 + 类别 + 是否不可逆）。密钥/密码一律不可逆，邮箱/电话可逆占位。
_BUILTIN_PATTERNS: list[tuple[str, str, str, bool, bool, bool]] = [
    # name, regex, category, irreversible, block, safe
    # 允许 @ 后/顶级域前出现空白或换行，防“example.\ncom”式字符插入绕过；误报率低（必须含 @ 与 TLD）。
    ("EMAIL", r"[A-Za-z0-9._%+\-]+@\s*[A-Za-z0-9.\-]+\.\s*[A-Za-z]{2,}", "email", False, False, True),
    ("IPV4", r"(?<!\d)(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}(?!\d)", "ip", True, False, True),
    ("IPV6", r"(?<![A-Fa-f0-9:])(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}(?![A-Fa-f0-9:])", "ip", True, False, True),
    ("PHONE_CN", r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "phone", False, False, True),
    ("PHONE_GENERIC", r"(?<![0-9A-Fa-f])\+?[1-9]\d{6,14}(?![0-9A-Fa-f])", "phone", False, False, True),
    ("CN_ID", r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)", "id_card", True, False, True),
    ("CREDIT_CARD", r"(?<!\d)(?:4\d{3}|5[1-5]\d{2}|6\d{3}|3[47]\d{2})[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4,7}(?!\d)", "credit_card", True, False, True),
    ("API_KEY_SK", r"(?i)\bsk-[A-Za-z0-9_\-]{16,}\b", "api_key", True, False, True),
    ("JWT", r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b", "api_key", True, False, True),
    ("AWS_KEY", r"\bAKIA[0-9A-Z]{16}\b", "api_key", True, False, True),
    ("PEM_PRIVATE", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "private_key", True, False, True),
    ("SSH_PRIVATE", r"-----BEGIN OPENSSH PRIVATE KEY-----[\s\S]*?-----END OPENSSH PRIVATE KEY-----", "private_key", True, False, True),
    ("PASSWORD_ASSIGN", r"(?i)(?:password|passwd|pwd|secret|api[_-]?key|token|access[_-]?key)['\"]?\s*[:=]\s*['\"]?[^\s'\"&,;]{4,}", "secret", True, False, True),
    ("URL_CREDENTIALS", r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/@:]+:[^\s/@]+@", "secret", True, False, True),
    ("GITHUB_TOKEN", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "api_key", True, False, True),
    ("DISCORD_TOKEN", r"\b(?:mfa\.[A-Za-z0-9_\-]{80,}|[A-Za-z0-9_\-]{24}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{25,})\b", "api_key", True, False, True),
]


_DEFAULT_KEYWORDS = [
    "password", "passwd", "pwd", "secret", "api_key", "api-key", "apikey",
    "token", "private", "confidential", "credentials", "credential",
    "密码", "口令", "密钥", "令牌", "私密", "机密", "账号", "账户",
    "邮箱", "手机号", "身份证", "银行卡", "地址", "ip地址", "家庭住址",
]

# 上下文敏感关键词：中文要求关键词后出现疑似值；英文要求赋值式或紧邻值（避免“token endpoint”误报）。
_CN_CONTEXT_KEYWORDS = ["密码", "口令", "密钥", "账号", "邮箱", "手机号", "身份证", "银行卡", "ip地址", "家庭住址"]
_EN_CONTEXT_KEYWORDS = ["password", "passwd", "pwd", "secret", "token"]

_CN_CONTEXT_ALLOWLIST = {
    "策略", "管理", "规则", "认证", "校验", "检查", "设置", "配置", "系统", "服务",
    "政策", "加密", "重置", "找回", "修改", "输入", "错误", "过期", "格式", "长度",
    "强度", "安全", "机制", "中心", "管理器", "提示", "错误提示", "要求", "规范",
    "体系",
}
_EN_CONTEXT_ALLOWLIST = {
    "endpoint", "auth", "header", "value", "file", "url", "path", "key", "based",
    "authentication", "access", "ttl", "refresh", "public", "private", "handler",
    "store", "management", "rotation", "expiry", "expires", "check", "validate",
    "verify", "service", "manager", "config", "configuration", "strategy", "policy",
    "rules", "settings", "system", "reset", "recovery", "change", "input", "error",
    "expired", "format", "length", "strength", "security", "mechanism", "rotation",
    "handling", "exposure", "logging", "storing", "storage", "commit", "committing",
}
_EN_CONTEXT_STOPWORDS = {
    "and", "or", "but", "in", "on", "of", "to", "for", "with", "from", "by", "at",
    "as", "the", "a", "an", "is", "are", "was", "were", "be", "should", "must",
    "never", "always", "do", "not", "into", "within", "during", "over", "under",
    "about", "through", "between", "among", "after", "before", "all", "any",
    "your", "their", "our", "its", "it", "this", "that", "these", "those", "no", "yes",
}


def _looks_like_secret(val: str) -> bool:
    """English context value heuristic: only flag values that look like secrets."""
    low = val.lower().rstrip(".,!?;:'\"")
    if low.startswith(("sk-", "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "eyj", "ak")):
        return True
    return len(val) >= 4 and (
        any(c.isdigit() for c in low) or any(c in "@#$%^&*!+=?~" for c in low)
    )

_CN_VALUE_RE = re.compile(r"[\s:：=是为属叫号]*(?P<value>[^\s，。；,!?、；]{2,80})")


# 编码候选提取：base64 token（含 padding 与 urlsafe）、连续 hex、空格分隔 hex
_B64_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_\-]{12,}={0,2}")
_HEX_CONT_RE = re.compile(r"[0-9a-fA-F]{12,}")
_HEX_SPACED_RE = re.compile(r"(?:[0-9a-fA-F]{2}\s*){6,}[0-9a-fA-F]{0,2}")
_DECODE_MAX_VARIANTS = 96
_DECODE_MAX_DEPTH = 3


def _decoded_variants(text: str) -> list[str]:
    """编码绕过防御（递归）：
    1. 从文本中提取 base64 / hex 候选片段再解码（支持带前缀/夹在句子中的编码）；
    2. URL 解码与 NFKC 全角归一化；
    3. 解码结果继续递归（最多 3 层），覆盖 base64→hex 等双重编码；
    4. 数量有界，防恶意超大输入拖垮检测。
    """
    seen: list[str] = []
    queue: list[tuple[str, int]] = [(text, 0)]
    while queue and len(seen) < _DECODE_MAX_VARIANTS:
        cur, depth = queue.pop(0)
        if depth >= _DECODE_MAX_DEPTH:
            continue

        dec = urllib.parse.unquote(cur)
        if dec != cur:
            if dec not in seen:
                seen.append(dec)
            queue.append((dec, depth + 1))

        nfkc = unicodedata.normalize("NFKC", cur)
        if nfkc != cur:
            if nfkc not in seen:
                seen.append(nfkc)
            queue.append((nfkc, depth + 1))

        for tok in _B64_TOKEN_RE.findall(cur):
            padded = tok.replace("-", "+").replace("_", "/") + "=" * (-len(tok) % 4)
            for decoder in (base64.b64decode, base64.urlsafe_b64decode):
                try:
                    decoded = decoder(padded).decode("utf-8", errors="replace")
                except Exception:
                    continue
                if decoded and decoded != cur and decoded not in seen:
                    seen.append(decoded)
                    queue.append((decoded, depth + 1))

        for tok in _HEX_CONT_RE.findall(cur):
            if len(tok) % 2:
                continue
            try:
                decoded = bytes.fromhex(tok).decode("utf-8", errors="replace")
            except (ValueError, binascii.Error):
                continue
            if decoded and decoded != cur and decoded not in seen:
                seen.append(decoded)
                queue.append((decoded, depth + 1))

        for tok in _HEX_SPACED_RE.findall(cur):
            compact = re.sub(r"\s+", "", tok)
            if not compact or len(compact) % 2:
                continue
            try:
                decoded = bytes.fromhex(compact).decode("utf-8", errors="replace")
            except (ValueError, binascii.Error):
                continue
            if decoded and decoded != cur and decoded not in seen:
                seen.append(decoded)
                queue.append((decoded, depth + 1))
    return seen


def _mask_intervals(text: str, matches: list[Match]) -> tuple[str, dict[str, str]]:
    """按区间合并做掩码：重叠区间取并集；含不可逆命中则整段不可逆掩码，否则可逆占位。"""
    if not matches:
        return text, {}
    intervals = sorted((m.start, m.end) for m in matches)
    merged: list[list[int]] = [list(intervals[0])]
    for s, e in intervals[1:]:
        if s <= merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1][1] = e
        else:
            merged.append([s, e])
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}
    chunks: list[str] = []
    pos = 0
    for s, e in merged:
        chunks.append(text[pos:s])
        covered = [m for m in matches if m.start < e and m.end > s]
        irreversible = [m for m in covered if m.rule.irreversible]
        if irreversible:
            # 优先用跨度最大的不可逆规则命名，避免一律 secret 掩盖更具体类别
            chosen = max(irreversible, key=lambda m: (m.end - m.start, -m.start))
            chunks.append(f"[REDACTED_{chosen.rule.category.upper()}]")
        else:
            m0 = covered[0]
            n = counters.get(m0.rule.category, 0) + 1
            counters[m0.rule.category] = n
            ph = f"<{m0.rule.category.upper()}_{n}>"
            mapping[ph] = text[s:e]
            chunks.append(ph)
        pos = e
    chunks.append(text[pos:])
    return "".join(chunks), mapping


def _compile_rules(config: PrivacyConfig) -> list[PatternRule]:
    rules: list[PatternRule] = []
    for name, regex, category, irreversible, block, safe in _BUILTIN_PATTERNS:
        try:
            rules.append(PatternRule(name, re.compile(regex), category, irreversible, block, safe))
        except re.error as exc:  # pragma: no cover
            raise ValueError(f"内置规则 {name} 编译失败: {exc}") from exc
    for item in config.custom_patterns:
        name = str(item.get("name", "CUSTOM"))
        try:
            rules.append(
                PatternRule(
                    name,
                    re.compile(str(item["regex"])),
                    str(item.get("category", "custom")),
                    bool(item.get("irreversible", True)),
                    bool(item.get("block", False)),
                    bool(item.get("safe", True)),
                )
            )
        except (KeyError, re.error) as exc:
            raise ValueError(f"自定义规则 {name} 无效: {exc}") from exc
    return rules


def _iter_strings(obj: Any, scan_keys: bool = False) -> Iterable[tuple[str, str]]:
    """递归产出 (路径, 字符串值)。默认只扫值、不扫 JSON key（与 PrivAiTe 威胁模型一致）。"""
    if isinstance(obj, str):
        yield "", obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if scan_keys and isinstance(k, str):
                yield f".{k}", k
            for sub in _iter_strings(v, scan_keys=scan_keys):
                yield f".{k}{sub[0]}", sub[1]
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            for sub in _iter_strings(v, scan_keys=scan_keys):
                yield f"[{i}]{sub[0]}", sub[1]


class Redactor:
    """单请求内可逆脱敏：真实值 -> <TYPE_n>；不可逆类型 -> [REDACTED]。"""

    def __init__(self, rules: list[PatternRule]):
        self.rules = rules

    def scan(self, text: str, vault_paths: list[str]) -> ScanResult:
        result = ScanResult()
        seen: list[Match] = []
        for rule in self.rules:
            for m in rule.pattern.finditer(text):
                if rule.block:
                    result.blocked = True
                seen.append(Match(rule, m.start(), m.end(), m.group(0)))
        for path in vault_paths:
            if path and path in text.lower():
                rule = PatternRule("VAULT_PATH", re.compile(re.escape(path), re.IGNORECASE), "vault_path", True, False, True)
                for m in rule.pattern.finditer(text):
                    seen.append(Match(rule, m.start(), m.end(), m.group(0)))
        # 去重 + 按位置排序
        seen.sort(key=lambda m: (m.start, m.end))
        dedup: list[Match] = []
        last_end = -1
        for m in seen:
            if m.start >= last_end:
                dedup.append(m)
                last_end = m.end
        result.matches = dedup
        # 类别按全部命中计算（含被去重的重叠命中），避免“token: ghp_xxx”只报 secret 不报 api_key
        result.categories = {m.rule.category for m in seen}
        if dedup:
            result.sensitive = True
        return result

    def redact(self, text: str, scan: ScanResult) -> tuple[str, dict[str, str]]:
        mapping: dict[str, str] = {}
        counters: dict[str, int] = {}
        chunks: list[str] = []
        pos = 0
        for m in scan.matches:
            chunks.append(text[pos : m.start])
            if m.rule.irreversible:
                chunks.append(f"[REDACTED_{m.rule.category.upper()}]")
            else:
                n = counters.get(m.rule.category, 0) + 1
                counters[m.rule.category] = n
                ph = f"<{m.rule.category.upper()}_{n}>"
                mapping[ph] = m.value
                chunks.append(ph)
            pos = m.end
        chunks.append(text[pos:])
        return "".join(chunks), mapping

    def restore(self, text: str, mapping: dict[str, str]) -> str:
        if not mapping:
            return text
        # 长占位符优先，避免 <EMAIL_1> 被 <EMAIL_1>2 之类前缀误替换
        for ph in sorted(mapping, key=len, reverse=True):
            text = text.replace(ph, mapping[ph])
        return text


class StreamRestorer:
    """SSE 流式响应还原：占位符可能被 chunk 边界拆开，保留尾部缓冲。"""

    def __init__(self, mapping: dict[str, str], max_ph_len: int = 64):
        self.mapping = mapping
        self.buffer = ""
        self.max_ph_len = max_ph_len

    def process(self, chunk: str) -> str:
        self.buffer += chunk
        if len(self.buffer) <= self.max_ph_len:
            return ""
        cut = len(self.buffer) - self.max_ph_len
        emit, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return self._restore(emit)

    def flush(self) -> str:
        out = self._restore(self.buffer)
        self.buffer = ""
        return out

    def _restore(self, text: str) -> str:
        if not self.mapping:
            return text
        for ph in sorted(self.mapping, key=len, reverse=True):
            text = text.replace(ph, self.mapping[ph])
        return text


class SessionSticky:
    """会话粘性：首次命中敏感后，会话在 TTL 内一直走安全通道（LiteLLM 模式）。"""

    def __init__(self, enabled: bool, ttl_s: float):
        self.enabled = enabled
        self.ttl_s = ttl_s
        self._pins: dict[str, float] = {}
        self._lock = threading.Lock()

    def pin(self, session_id: str | None) -> None:
        if not self.enabled or not session_id:
            return
        with self._lock:
            self._pins[session_id] = time.time() + self.ttl_s

    def is_pinned(self, session_id: str | None) -> bool:
        if not self.enabled or not session_id:
            return False
        with self._lock:
            expires = self._pins.get(session_id)
            if expires is None:
                return False
            if time.time() > expires:
                del self._pins[session_id]
                return False
            return True

    def sweep(self) -> None:
        now = time.time()
        with self._lock:
            for sid in [s for s, exp in self._pins.items() if now > exp]:
                del self._pins[sid]


class PrivacyEngine:
    def __init__(self, config: PrivacyConfig):
        self.config = config
        self.rules = _compile_rules(config)
        self.redactor = Redactor(self.rules)
        self.sticky = SessionSticky(config.sticky_session, config.session_ttl_s)
        # 仅用户显式配置的关键词做低置信全匹配；默认关键词走上下文规则，避免“token endpoint”误报。
        self.keywords = [k.lower() for k in config.keywords]
        self.vault_paths = [p.lower() for p in config.vault_paths]

    def _context_matches(self, text: str) -> list[Match]:
        matches: list[Match] = []
        low = text.lower()
        rule_cn = PatternRule("CN_CONTEXT", re.compile(""), "secret", True, False, True)
        for kw in _CN_CONTEXT_KEYWORDS:
            idx = 0
            while True:
                pos = low.find(kw, idx)
                if pos < 0:
                    break
                after = text[pos + len(kw) : pos + len(kw) + 80]
                m = _CN_VALUE_RE.match(after)
                if m and not any(
                    m.group("value").lower().startswith(w) for w in _CN_CONTEXT_ALLOWLIST
                ):
                    end = pos + len(kw) + m.end()
                    matches.append(Match(rule_cn, pos, end, text[pos:end]))
                idx = pos + len(kw)
        rule_en = PatternRule("EN_CONTEXT", re.compile(""), "secret", True, False, True)
        for kw in _EN_CONTEXT_KEYWORDS:
            for m in re.finditer(rf"\b{re.escape(kw)}\b", low):
                after = text[m.end() : m.end() + 60]
                head = re.match(r"[\s:=]+", after)
                pos = head.end() if head else 0
                copula = re.match(r"(?:is|are|was|were|equals?)\s+", after[pos:])
                if copula:
                    pos += copula.end()
                vm = re.match(r"(?P<val>[A-Za-z0-9_@#$%^&*!+\-=?.'\"]{2,40})", after[pos:])
                if (
                    vm
                    and vm.group("val").lower() not in _EN_CONTEXT_ALLOWLIST
                    and vm.group("val").lower() not in _EN_CONTEXT_STOPWORDS
                    and _looks_like_secret(vm.group("val"))
                    and not vm.group("val").lower().startswith(("http://", "https://"))
                ):
                    end = m.end() + pos + vm.end()
                    matches.append(Match(rule_en, m.start(), end, text[m.start() : end]))
        return matches

    def _scan_text_full(self, text: str) -> ScanResult:
        s = self.redactor.scan(text, self.vault_paths)
        ctx = self._context_matches(text)
        if ctx:
            s.sensitive = True
            s.categories |= {m.rule.category for m in ctx}
            s.matches.extend(ctx)
        if not s.sensitive:
            for variant in _decoded_variants(text):
                vs = self.redactor.scan(variant, self.vault_paths)
                vctx = self._context_matches(variant)
                if vs.sensitive or vctx:
                    s.sensitive = True
                    s.categories |= vs.categories | {m.rule.category for m in vctx}
                    s.encoded_bypass = True
                    break
        return s

    def _redact_text_full(self, text: str) -> tuple[str, dict[str, str]]:
        s = self._scan_text_full(text)
        if s.encoded_bypass:
            # 无法把解码后的位置映射回原文，整体不可逆掩码，绝不回填
            return "[REDACTED_ENCODED]", {}
        return _mask_intervals(text, s.matches)

    def scan_payload(self, payload: dict[str, Any]) -> tuple[ScanResult, dict[str, str] | None]:
        """扫描请求体，返回 (结果, 脱敏映射 或 None)。mapping 只在 mode=redact 时生成。"""
        scan = ScanResult()
        mapping: dict[str, str] | None = None

        # 用户显式配置的关键字命中视为敏感（低置信但安全优先）
        texts = list(_iter_strings(payload, scan_keys=False))
        for _, text in texts:
            low = text.lower()
            for kw in self.keywords:
                if kw in low:
                    scan.sensitive = True
                    scan.categories.add("keyword")
                    break

        # 递归扫描所有字符串值（含工具调用参数、multimodal 文本等常见漏扫面）
        def deep_scan(obj: Any) -> None:
            nonlocal scan, mapping
            if isinstance(obj, dict):
                for val in obj.values():
                    deep_scan(val)
            elif isinstance(obj, list):
                for item in obj:
                    deep_scan(item)
            elif isinstance(obj, str):
                s = self._scan_text_full(obj)
                if s.sensitive:
                    scan.sensitive = True
                    scan.categories |= s.categories
                    scan.blocked = scan.blocked or s.blocked
                    scan.encoded_bypass = scan.encoded_bypass or s.encoded_bypass
                    scan.matches.extend(s.matches)
                if s.blocked:
                    scan.blocked = True

        deep_scan(payload)

        # 跨字符串拼接绕过：把全部字符串值无分隔拼接后重扫（仅判定）。
        # safe_route/local 直接路由安全通道；redact 模式无法把命中映射回原文，fail-closed 拒绝。
        if not scan.sensitive and len(texts) > 1:
            joined = "".join(t for _, t in texts)
            js = self._scan_text_full(joined)
            if js.sensitive:
                scan.sensitive = True
                scan.categories |= js.categories
                scan.encoded_bypass = scan.encoded_bypass or js.encoded_bypass
                if self.config.mode == "redact":
                    scan.blocked = True

        if self.config.mode == "redact" and scan.sensitive:
            mapping = {}
            # 重新逐字符串脱敏
            def redact_obj(obj: Any) -> Any:
                nonlocal mapping
                if isinstance(obj, dict):
                    return {k: redact_obj(v) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [redact_obj(v) for v in obj]
                if isinstance(obj, str):
                    new_text, m = self._redact_text_full(obj)
                    mapping.update(m)
                    return new_text
                return obj

            new_payload = redact_obj(payload)
            payload.clear()
            payload.update(new_payload)

        if scan.blocked:
            scan.sensitive = True
        return scan, (mapping if self.config.mode == "redact" else None)

    def redact_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """强制脱敏（供 safe_route 双保险或 local 模式使用）。"""
        mapping: dict[str, str] = {}

        def redact_obj(obj: Any) -> Any:
            nonlocal mapping
            if isinstance(obj, dict):
                return {k: redact_obj(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [redact_obj(v) for v in obj]
            if isinstance(obj, str):
                new_text, m = self._redact_text_full(obj)
                mapping.update(m)
                return new_text
            return obj

        return redact_obj(payload), mapping

    def scan_text(self, text: str) -> ScanResult:
        return self._scan_text_full(text)

    def restore(self, text: str, mapping: dict[str, str]) -> str:
        return self.redactor.restore(text, mapping)


def redact_json(payload: dict[str, Any], engine: PrivacyEngine) -> tuple[dict[str, Any], dict[str, str]]:
    return engine.redact_payload(payload)


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
