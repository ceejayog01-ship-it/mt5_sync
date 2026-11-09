from datetime import datetime, timezone
from config import SESSION_LONDON_START, SESSION_LONDON_END, SESSION_NY_START, SESSION_NY_END


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse(hhmm: str) -> tuple[int, int]:
    h, m = hhmm.split(":")
    return int(h), int(m)


def is_active_session(ts: datetime | None = None) -> bool:
    ts = ts or now_utc()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    minutes = ts.hour * 60 + ts.minute
    ls, le = _parse(SESSION_LONDON_START), _parse(SESSION_LONDON_END)
    ns, ne = _parse(SESSION_NY_START), _parse(SESSION_NY_END)
    london = ls[0] * 60 + ls[1] <= minutes < le[0] * 60 + le[1]
    ny = ns[0] * 60 + ns[1] <= minutes < ne[0] * 60 + ne[1]
    return london or ny
