from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from feed import badges


class _Q:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.log.append((name, args))
            return self

        return call

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _DB:
    def __init__(self, rows):
        self.rows, self.log = rows, []

    def table(self, name):
        self.log.append(("table", (name,)))
        return _Q(self.rows, self.log)


def test_reposted_ids_reads_the_last_24_hours():
    db = _DB([{"listing_id": "a"}, {"listing_id": "b"}])
    assert badges.reposted_ids(db, ["a", "b", "c"]) == {"a", "b"}
    assert ("table", ("listing_reposts",)) in db.log
    cutoff = next(args[1] for name, args in db.log if name == "gte")
    age = datetime.now(timezone.utc) - datetime.fromisoformat(cutoff)
    assert timedelta(hours=23, minutes=59) < age < timedelta(hours=24, minutes=1)


def test_reposted_ids_never_breaks_the_feed():
    class Broken:
        def table(self, _name):
            raise RuntimeError("down")

    assert badges.reposted_ids(Broken(), ["a"]) == set()
    assert badges.reposted_ids(None, ["a"]) == set()
    assert badges.reposted_ids(_DB([]), []) == set()
