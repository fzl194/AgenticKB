from __future__ import annotations


class PreprocessingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message

    def as_metadata(self) -> dict[str, str]:
        return {
            "preprocess_status": "failed",
            "preprocess_error_code": self.code,
            "preprocess_error": self.safe_message,
        }
