from datetime import timezone

from backend.utils.time import naive_utc_now, utc_now


def test_utc_now_is_timezone_aware_utc():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timezone.utc.utcoffset(None)


def test_naive_utc_now_has_no_tzinfo():
    now = naive_utc_now()
    assert now.tzinfo is None
