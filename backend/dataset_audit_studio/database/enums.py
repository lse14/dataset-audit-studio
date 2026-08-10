from enum import StrEnum


class TaskStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    SCANNING = "scanning"
    CPU_METRICS = "cpu_metrics"
    MODEL_SCORING = "model_scoring"
    AWAITING_AI_REVIEW = "awaiting_ai_review"
    STYLE_ANALYSIS = "style_analysis"
    SEMANTIC_CLUSTERING = "semantic_clustering"
    EVIDENCE_REVIEW = "evidence_review"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    PAUSING = "pausing"
    PAUSED = "paused"
    TERMINATING = "terminating"
    TERMINATED = "terminated"
    FAILED = "failed"


class ArtifactState(StrEnum):
    WRITING = "writing"
    READY = "ready"
    INVALID = "invalid"


class ComponentRunState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    FAILED = "failed"


class ReviewState(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED_KEEP = "approved_keep"
    APPROVED_EXCLUDE = "approved_exclude"


class ExportRunStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    COPYING = "copying"
    VERIFYING = "verifying"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
