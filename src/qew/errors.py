"""Failures with stable command exit semantics."""


class QewError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code



class NotFound(QewError):
    """A reference that matches no item or pull request in the ledger as read: the other machine's work may not be pulled yet."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 2)
