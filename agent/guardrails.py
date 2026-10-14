"""Public answer guardrails; implementations live in agent.safety."""
from agent.safety import (
    GroundingResult, check_grounding, InjectionScanResult,
    scan_for_injected_instructions, RedactionResult, redact,
    ArithmeticCheckResult, verify_arithmetic, abstention_policy,
    AnswerCheckResult, validate_answer,
)
