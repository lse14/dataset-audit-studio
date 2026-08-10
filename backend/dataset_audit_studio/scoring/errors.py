class ScoringError(RuntimeError):
    pass


class ModelAssetDownloadError(ScoringError):
    pass


class ScoringProcessError(ScoringError):
    pass
