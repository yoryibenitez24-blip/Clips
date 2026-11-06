class ClipsError(Exception):
    """Base class for user-facing processing errors."""

    code = "error"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status


class InvalidURL(ClipsError):
    code = "invalid_url"


class UnsupportedSource(ClipsError):
    code = "unsupported_source"


class AccessDenied(ClipsError):
    """Media retrieval is not permitted / not authorized for this source."""

    code = "media_access_denied"
    http_status = 403


class RetrievalFailed(ClipsError):
    code = "retrieval_failed"
    http_status = 502


class LimitExceeded(ClipsError):
    code = "limit_exceeded"
    http_status = 413


class ProcessingFailed(ClipsError):
    code = "processing_failed"
    http_status = 500


class SourceUnavailable(ClipsError):
    code = "source_unavailable"
    http_status = 409
