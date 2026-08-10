class TaskDomainError(RuntimeError):
    """Base class for task control failures."""


class LegacyTaskConfigUnsupported(ValueError, TaskDomainError):
    """A profile-free task is outside the supported R2 workflow."""


class TaskNotFound(TaskDomainError):
    pass


class InvalidTaskTransition(TaskDomainError):
    pass


class TaskVersionConflict(TaskDomainError):
    pass


class WorkerLeaseUnavailable(TaskDomainError):
    pass


class StaleWorkerToken(TaskDomainError):
    pass


class CheckpointConflict(TaskDomainError):
    pass
