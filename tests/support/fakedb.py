"""In-memory stand-in for the Supabase/PostgREST client used by the API.

Supports the builder surface the codebase uses (filters, ordering, paging,
insert/update/upsert/delete, rpc) so route and service tests exercise real
logic without a network. Column projection and embedded selects are ignored:
tests seed the already-joined shape the code reads.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable


class FakeAPIError(Exception):
    """Raised where PostgREST would answer with an error status."""

    def __init__(self, message: str, code: str = "PGRST000") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class Result:
    def __init__(self, data: Any, count: int | None = None) -> None:
        self.data = data
        self.count = count


def _norm(value: Any) -> Any:
    return str(value) if isinstance(value, uuid.UUID) else value


def _cmp_key(value: Any) -> tuple[int, Any]:
    return (1, "") if value is None else (0, value)


def _like_regex(pattern: str) -> re.Pattern[str]:
    escaped = re.escape(pattern).replace("%", ".*").replace("_", ".")
    return re.compile(f"^{escaped}$", re.IGNORECASE | re.DOTALL)


def _apply_op(row: dict, col: str, op: str, value: Any) -> bool:
    cell = _norm(row.get(col))
    value = _norm(value)
    if op == "eq":
        return cell == value or (cell is not None and value is not None and str(cell) == str(value))
    if op == "neq":
        return not _apply_op(row, col, "eq", value)
    if op == "is":
        want_null = value in (None, "null")
        if value in ("true", "false", True, False):
            return cell is (value in ("true", True))
        return (cell is None) == want_null
    if op == "in":
        values = value if isinstance(value, (list, tuple, set)) else str(value).strip("()").split(",")
        return str(cell) in {str(v) for v in values} if cell is not None else False
    if op in {"gt", "gte", "lt", "lte"}:
        if cell is None or value is None:
            return False
        try:
            a, b = (cell, value) if not isinstance(cell, str) else (str(cell), str(value))
            return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
        except TypeError:
            return False
    if op in {"ilike", "like"}:
        return cell is not None and bool(_like_regex(str(value)).match(str(cell)))
    if op in {"contains", "cs"}:
        want = value if isinstance(value, (list, tuple, set)) else [value]
        have = cell or []
        return all(item in have for item in want)
    if op in {"overlaps", "ov"}:
        want = value if isinstance(value, (list, tuple, set)) else [value]
        return any(item in (cell or []) for item in want)
    raise NotImplementedError(f"fake supabase does not support operator {op!r}")


def _parse_or(expr: str) -> list[tuple[str, str, Any]]:
    clauses = []
    for part in re.split(r",(?![^()]*\))", expr):
        col, op, val = part.split(".", 2)
        if op == "in":
            val = [v for v in val.strip("()").split(",") if v]
        clauses.append((col, op, val))
    return clauses


class Query:
    def __init__(self, db: "FakeSupabase", table: str) -> None:
        self._db = db
        self._table = table
        self._op = "select"
        self._payload: Any = None
        self._filters: list[Callable[[dict], bool]] = []
        self._order: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._offset = 0
        self._count = False
        self._single: str | None = None
        self._negate = False
        self._on_conflict: str | None = None

    # -- verbs ---------------------------------------------------------
    def select(self, *_cols: str, count: str | None = None, **_kw: Any) -> "Query":
        self._count = bool(count)
        return self

    def insert(self, payload: Any, **_kw: Any) -> "Query":
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload: dict, **_kw: Any) -> "Query":
        self._op, self._payload = "update", payload
        return self

    def upsert(self, payload: Any, on_conflict: str | None = None, **_kw: Any) -> "Query":
        self._op, self._payload, self._on_conflict = "upsert", payload, on_conflict
        return self

    def delete(self, **_kw: Any) -> "Query":
        self._op = "delete"
        return self

    # -- filters -------------------------------------------------------
    def _add(self, col: str, op: str, value: Any) -> "Query":
        negate, self._negate = self._negate, False
        self._filters.append(lambda r: _apply_op(r, col, op, value) != negate)
        return self

    def eq(self, col: str, value: Any) -> "Query":
        return self._add(col, "eq", value)

    def neq(self, col: str, value: Any) -> "Query":
        return self._add(col, "neq", value)

    def in_(self, col: str, values: Any) -> "Query":
        return self._add(col, "in", list(values))

    def gt(self, col: str, value: Any) -> "Query":
        return self._add(col, "gt", value)

    def gte(self, col: str, value: Any) -> "Query":
        return self._add(col, "gte", value)

    def lt(self, col: str, value: Any) -> "Query":
        return self._add(col, "lt", value)

    def lte(self, col: str, value: Any) -> "Query":
        return self._add(col, "lte", value)

    def is_(self, col: str, value: Any) -> "Query":
        return self._add(col, "is", value)

    def ilike(self, col: str, pattern: str) -> "Query":
        return self._add(col, "ilike", pattern)

    def like(self, col: str, pattern: str) -> "Query":
        return self._add(col, "like", pattern)

    def contains(self, col: str, value: Any) -> "Query":
        return self._add(col, "contains", value)

    def overlaps(self, col: str, value: Any) -> "Query":
        return self._add(col, "overlaps", value)

    def filter(self, col: str, op: str, value: Any) -> "Query":
        return self._add(col, op, value)

    def match(self, mapping: dict) -> "Query":
        for col, value in mapping.items():
            self.eq(col, value)
        return self

    def or_(self, expr: str, **_kw: Any) -> "Query":
        clauses = _parse_or(expr)
        self._filters.append(lambda r: any(_apply_op(r, c, o, v) for c, o, v in clauses))
        return self

    @property
    def not_(self) -> "Query":
        self._negate = True
        return self

    # -- shaping -------------------------------------------------------
    def order(self, col: str, desc: bool = False, **_kw: Any) -> "Query":
        self._order.append((col, bool(desc) or _kw.get("ascending") is False))
        return self

    def limit(self, n: int, **_kw: Any) -> "Query":
        self._limit = n
        return self

    def range(self, start: int, end: int, **_kw: Any) -> "Query":
        self._offset, self._limit = start, end - start + 1
        return self

    def offset(self, n: int) -> "Query":
        self._offset = n
        return self

    def single(self) -> "Query":
        self._single = "single"
        return self

    def maybe_single(self) -> "Query":
        self._single = "maybe"
        return self

    # -- execution -----------------------------------------------------
    def _matching(self) -> list[dict]:
        return [r for r in self._db.tables.setdefault(self._table, []) if all(f(r) for f in self._filters)]

    def _sorted(self, rows: list[dict]) -> list[dict]:
        for col, desc in reversed(self._order):
            rows = sorted(rows, key=lambda r: _cmp_key(r.get(col)), reverse=desc)
        return rows

    def execute(self) -> Result:
        self._db.calls.append((self._table, self._op, self._payload))
        failure = self._db.failures.get((self._table, self._op))
        if failure is not None:
            raise failure
        handler = getattr(self, f"_run_{self._op}")
        return handler()

    def _finish(self, rows: list[dict], total: int | None = None) -> Result:
        rows = [dict(r) for r in rows]
        if self._single:
            if len(rows) != 1:
                if self._single == "maybe" and not rows:
                    return Result(None, total)
                raise FakeAPIError("JSON object requested, multiple (or no) rows returned", "PGRST116")
            return Result(rows[0], total)
        return Result(rows, total)

    def _run_select(self) -> Result:
        rows = self._sorted(self._matching())
        total = len(rows) if self._count else None
        rows = rows[self._offset:]
        if self._limit is not None:
            rows = rows[: self._limit]
        return self._finish(rows, total)

    def _prepare_row(self, row: dict) -> dict:
        new = dict(row)
        new.setdefault("id", str(uuid.uuid4()))
        new.setdefault("created_at", self._db.now_iso())
        return new

    def _check_unique(self, row: dict, ignore: dict | None = None) -> None:
        for cols in self._db.uniques.get(self._table, []):
            if any(row.get(c) is None for c in cols):
                continue
            for other in self._db.tables.setdefault(self._table, []):
                if other is ignore:
                    continue
                if all(str(other.get(c)) == str(row.get(c)) for c in cols):
                    raise FakeAPIError(
                        f'duplicate key value violates unique constraint ({",".join(cols)})', "23505"
                    )

    def _run_insert(self) -> Result:
        payload = self._payload if isinstance(self._payload, list) else [self._payload]
        created = []
        for row in payload:
            new = self._prepare_row(row)
            self._check_unique(new)
            self._db.tables.setdefault(self._table, []).append(new)
            created.append(new)
        return self._finish(created)

    def _run_update(self) -> Result:
        hit = self._matching()
        for row in hit:
            updated = {**row, **self._payload}
            self._check_unique(updated, ignore=row)
            row.update(self._payload)
        return self._finish(hit)

    def _run_upsert(self) -> Result:
        payload = self._payload if isinstance(self._payload, list) else [self._payload]
        keys = [k.strip() for k in (self._on_conflict or "id").split(",")]
        out = []
        for row in payload:
            existing = next(
                (
                    r
                    for r in self._db.tables.setdefault(self._table, [])
                    if all(k in row and str(r.get(k)) == str(row[k]) for k in keys)
                ),
                None,
            )
            if existing is not None:
                existing.update(row)
                out.append(existing)
            else:
                new = self._prepare_row(row)
                self._check_unique(new)
                self._db.tables[self._table].append(new)
                out.append(new)
        return self._finish(out)

    def _run_delete(self) -> Result:
        hit = self._matching()
        ids = {id(r) for r in hit}
        self._db.tables[self._table] = [r for r in self._db.tables.get(self._table, []) if id(r) not in ids]
        return self._finish(hit)


class RpcCall:
    def __init__(self, db: "FakeSupabase", name: str, params: dict | None) -> None:
        self._db, self._name, self._params = db, name, params or {}

    def execute(self) -> Result:
        self._db.calls.append((f"rpc:{self._name}", "rpc", self._params))
        handler = self._db.rpcs.get(self._name)
        if handler is None:
            return Result(None)
        return Result(handler(self._params))


class FakeSupabase:
    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.rpcs: dict[str, Callable[[dict], Any]] = {}
        self.uniques: dict[str, list[tuple[str, ...]]] = {}
        self.failures: dict[tuple[str, str], Exception] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self._now = now or (lambda: datetime.now(timezone.utc))

    def now_iso(self) -> str:
        return self._now().isoformat()

    def table(self, name: str) -> Query:
        return Query(self, name)

    def rpc(self, name: str, params: dict | None = None) -> RpcCall:
        return RpcCall(self, name, params)

    # -- test helpers --------------------------------------------------
    def seed(self, table: str, *rows: dict) -> list[dict]:
        stored = []
        for row in rows:
            new = {"id": str(uuid.uuid4()), "created_at": self.now_iso(), **row}
            self.tables.setdefault(table, []).append(new)
            stored.append(new)
        return stored

    def rows(self, table: str, **where: Any) -> list[dict]:
        return [r for r in self.tables.get(table, []) if all(str(r.get(k)) == str(v) for k, v in where.items())]

    def one(self, table: str, **where: Any) -> dict:
        found = self.rows(table, **where)
        assert len(found) == 1, f"expected exactly one {table} row for {where}, found {len(found)}"
        return found[0]

    def unique(self, table: str, *cols: str) -> None:
        self.uniques.setdefault(table, []).append(tuple(cols))

    def fail(self, table: str, op: str, exc: Exception) -> None:
        self.failures[(table, op)] = exc

    def writes(self, table: str, op: str) -> list[Any]:
        return [p for t, o, p in self.calls if t == table and o == op]
