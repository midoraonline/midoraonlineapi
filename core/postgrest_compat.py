"""Handle PostgREST errors when optional DB columns (e.g. view_count) are not migrated yet."""

from postgrest.exceptions import APIError


def is_undefined_column_error(exc: BaseException, column_name: str = "view_count") -> bool:
    if not isinstance(exc, APIError):
        return False
    if exc.code != "42703":
        return False
    text = f"{exc.message or ''} {exc.details or ''}"
    return column_name in text
