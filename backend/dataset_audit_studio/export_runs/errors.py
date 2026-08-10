from __future__ import annotations


class ExportRunError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ExportRunNotFound(ExportRunError):
    def __init__(self, run_id: str) -> None:
        super().__init__("export_run_not_found", f"Export run not found: {run_id}")
