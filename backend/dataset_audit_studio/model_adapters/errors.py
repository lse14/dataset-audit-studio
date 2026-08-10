class ModelRegistryError(RuntimeError):
    """Base error for model registry and installation operations."""


class ModelIntegrityError(ModelRegistryError):
    pass


class ModelSchemaError(ModelRegistryError):
    pass


class ModelOperationConflict(ModelRegistryError):
    pass


class ModelOperationNotFound(ModelRegistryError):
    pass


class ModelDownloadCanceled(ModelRegistryError):
    pass
