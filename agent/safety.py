"""Deterministic answer checks; heuristics do not certify semantic truth."""
from __future__ import annotations
import ast
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from fractions import Fraction
from typing import Any, Iterable, Mapping
from kit.world.anchor import Anchor, AnchorSyntaxError


@dataclass(frozen=True, slots=True)
class GroundingResult:
    grounded: bool
    cited: tuple[str, ...]
    ungrounded: tuple[str, ...]
    malformed: tuple[str, ...]


def anchor_base(value):
    parsed = Anchor.parse(value)
    return str(Anchor(parsed.ns, parsed.slug, parsed.rev, parsed.idx))


def check_grounding(answer, retrieved_anchors, *, require_citation=True):
    raw = answer.get("cited_anchors") or ()
    cited = (raw,) if isinstance(raw, str) else tuple(raw)
    retrieved = set()
    for value in retrieved_anchors:
        try:
            retrieved.add(anchor_base(value))
        except (AnchorSyntaxError, TypeError):
            continue
    malformed, missing = [], []
    for value in cited:
        try:
            base = anchor_base(value)
        except (AnchorSyntaxError, TypeError):
            malformed.append(str(value))
            continue
        if base not in retrieved:
            missing.append(value)
    return GroundingResult(not malformed and not missing and (bool(cited) or not require_citation),
                           cited, tuple(missing), tuple(malformed))


@dataclass(frozen=True, slots=True)
class InjectionScanResult:
    suspicious: bool
    matched_patterns: tuple[str, ...]


_INJECTION_PATTERNS = {
    "override_instructions": r"\b(?:ignore|disregard|forget)\s+(?:all\s+|every\s+|the\s+)?(?:previous|prior|above)\s+(?:instructions?|rules?|safety)|\bsystem\s+override\b",
    "override_vi": r"bỏ\s+qua\s+(?:mọi|tất cả|các)\s+(?:hướng dẫn|chỉ dẫn|quy tắc)|bạn phải (?:tiết lộ|in ra)",
    "exfiltration": r"\b(?:reveal|disclose|print|send|exfiltrate)\b[^\n.!?]{0,100}\b(?:api[_ -]?key|secret|private grading key|password|access token)\b",
    "cross_learner_write": r"\b(?:also|instead)[, ]+(?:record|write|update)\b[^\n.!?]{0,80}\b(?:learner|sv-\d+)",
    "role_impersonation": r"(?:\[system\]|<system>|as the system[, :])[^\n]{0,100}(?:must|override|ignore)",
}


def scan_for_injected_instructions(text):
    matches = tuple(name for name, pattern in _INJECTION_PATTERNS.items()
                    if re.search(pattern, text or "", re.I))
    return InjectionScanResult(bool(matches), matches)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    redacted_text: str
    hits: tuple[str, ...]


def _normalise_with_offsets(text):
    chars, offsets = [], []
    for match in re.finditer(r"\S+", text):
        if chars:
            chars.append(" ")
            offsets.append(match.start())
        for index in range(match.start(), match.end()):
            lowered = text[index].casefold()
            chars.extend(lowered)
            offsets.extend([index] * len(lowered))
    return "".join(chars), offsets


def redact(text, *, private_snippets=()):
    """Remove known private excerpts (>=40 normalized chars) and identifiers."""
    text = text or ""
    normalized, offsets = _normalise_with_offsets(text)
    spans, hits = [], []
    for snippet in private_snippets:
        private, _ = _normalise_with_offsets(str(snippet))
        if len(private) < 40:
            continue
        for match in SequenceMatcher(None, private, normalized, autojunk=False).get_matching_blocks():
            if match.size >= 40:
                spans.append((offsets[match.b], offsets[match.b + match.size - 1] + 1))
                hits.append("private_content")
    patterns = {
        "email": r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "phone": r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)",
        "credential": r"\b(?:[A-Z_]*API_KEY|password|access_token|secret)\s*[:=]\s*[\"']?[^\s,;\"']{6,}",
    }
    for name, pattern in patterns.items():
        for match in re.finditer(pattern, text, re.I):
            if name == "phone" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", match.group()):
                continue
            spans.append(match.span())
            hits.append(name)
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        text = text[:start] + "[REDACTED]" + text[end:]
    return RedactionResult(text, tuple(sorted(set(hits))))


@dataclass(frozen=True, slots=True)
class ArithmeticCheckResult:
    checked: bool
    ok: bool | None
    detail: str


_EQUATION = re.compile(r"(?<![\w/])([+-]?\d+(?:\.\d+)?(?:\s*[+*/-]\s*[+-]?\d+(?:\.\d+)?)+)\s*=\s*([+-]?\d+(?:\.\d+)?)")


def _calculate(node, expression):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return Fraction(ast.get_source_segment(expression, node))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _calculate(node.operand, expression)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = _calculate(node.left, expression), _calculate(node.right, expression)
        if isinstance(node.op, ast.Add): return left + right
        if isinstance(node.op, ast.Sub): return left - right
        if isinstance(node.op, ast.Mult): return left * right
        if isinstance(node.op, ast.Div): return left / right
    raise ValueError("unsupported arithmetic expression")


def verify_arithmetic(text):
    """Check explicit equations with fractions, without executing code."""
    equations = list(_EQUATION.finditer(text or ""))
    if not equations:
        return ArithmeticCheckResult(False, None, "no explicit equation; source support remains unchecked")
    for match in equations:
        expression = match.group(1).strip()
        if len(expression) > 200:
            return ArithmeticCheckResult(True, False, "equation exceeds verification limit")
        try:
            value = _calculate(ast.parse(expression, mode="eval").body, expression)
            if value != Fraction(match.group(2)):
                return ArithmeticCheckResult(True, False, f"inconsistent equation: {expression}")
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
            return ArithmeticCheckResult(True, False, "invalid or undefined equation")
    return ArithmeticCheckResult(True, True, f"verified {len(equations)} equation(s)")


def abstention_policy(grounding):
    return not grounding.grounded


@dataclass(frozen=True, slots=True)
class AnswerCheckResult:
    answer: dict
    accepted: bool
    issues: tuple[str, ...]


def validate_answer(answer, retrieved_anchors, *, ask=None, private_snippets=()):
    candidate = dict(answer)
    text = str(candidate.get("text") or "")
    issues = []
    abstained = candidate.get("abstained") is True
    grounding = check_grounding(candidate, retrieved_anchors, require_citation=not abstained)
    if not grounding.grounded:
        issues.append("citation_not_retrieved")
    if scan_for_injected_instructions(text).suspicious and not re.search(
            r"not following|refus|will not|không (?:làm theo|tuân theo)", text, re.I):
        issues.append("instruction_in_answer")
    if not abstained:
        for field in (ask or {}).get("require", ()):
            if field not in candidate or candidate[field] is None:
                issues.append(f"missing_required_field:{field}")
    arithmetic = verify_arithmetic(text)
    if arithmetic.checked and arithmetic.ok is False:
        issues.append("arithmetic_mismatch")
    if issues:
        candidate = {"text": "Chưa đủ căn cứ đã xác minh để trả lời yêu cầu này.",
                     "cited_anchors": [], "abstained": True}
    else:
        candidate["text"] = redact(text, private_snippets=private_snippets).redacted_text
    return AnswerCheckResult(candidate, not issues, tuple(issues))
