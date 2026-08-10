"""Task state machine and worker orchestration."""

__all__ = ["TaskService"]


def __getattr__(name: str):
    if name == "TaskService":
        from dataset_audit_studio.jobs.service import TaskService

        return TaskService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
