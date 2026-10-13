from auth.routes import contact_verify as cv


class _Table:
    def __init__(self, log):
        self.log = log

    def update(self, payload):
        self.log.append(payload)
        return self

    def eq(self, *a):
        return self

    def execute(self):
        return None


class _Admin:
    def __init__(self, log):
        self.log = log

    def table(self, name):
        return _Table(self.log)


def _run(monkeypatch, row, phone="+256700000001"):
    log = []
    monkeypatch.setattr(cv, "_user_row", lambda uid: row)
    monkeypatch.setattr(cv, "get_supabase_admin", lambda: _Admin(log))
    cv.remember_unverified_phone("u", phone)
    return log


def test_stores_first_phone(monkeypatch):
    assert _run(monkeypatch, {"phone_number": None, "phone_verified": False}) == [{"phone_number": "+256700000001"}]


def test_never_overwrites_verified(monkeypatch):
    assert _run(monkeypatch, {"phone_number": "+256700000009", "phone_verified": True}) == []


def test_same_number_noop(monkeypatch):
    assert _run(monkeypatch, {"phone_number": "+256700000001", "phone_verified": False}) == []
