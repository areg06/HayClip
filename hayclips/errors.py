"""Typed errors. Every error carries a short operator hint; CLIs print `message` and `hint`, never tracebacks."""
from __future__ import annotations


class PipelineError(Exception):
    code = "pipeline_error"

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return f"{self.message}" + (f"\n  hint: {self.hint}" if self.hint else "")


class ValidationError(PipelineError):
    code = "invalid_input"


class SourceError(PipelineError):
    code = "source_error"


class LegacyLayoutError(PipelineError):
    code = "legacy_layout"


class ArtifactMismatch(PipelineError):
    """Stored record and bytes on disk disagree. Never reuse and never resubmit: investigate."""
    code = "artifact_mismatch"


class AlignmentError(PipelineError):
    """The caption timeline cannot be proven to match the render timeline."""
    code = "alignment_unverified"


class FontMissing(PipelineError):
    code = "font_missing"


class ConsentMissing(PipelineError):
    code = "consent_missing"


class BudgetExceeded(PipelineError):
    code = "budget_exceeded"


class PaidOperationBlocked(PipelineError):
    """A paid call was refused before any network traffic (no opt-in, no confirmation, test environment...)."""
    code = "paid_blocked"


class NeedsReconciliation(PipelineError):
    """A previous paid submission may have been accepted. A human must reconcile before anything else."""
    code = "needs_reconciliation"


class ProviderError(PipelineError):
    code = "provider_error"
