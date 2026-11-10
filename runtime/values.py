"""Reading Finvalda's values: exact decimals, dates and times, file names. No Finvalda calls."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from typing import Any


def decimal_of(value: Any) -> Decimal | None:
    """A number from Finvalda (JSON number, Decimal or text) as a Decimal, or None."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def exact(value: Any) -> Any:
    """Finvalda's numbers are read as Decimal; a result carries them as exact text."""
    if isinstance(value, Decimal):
        return format(value, "f") if value.is_finite() else None
    if isinstance(value, Mapping):
        return {k: exact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [exact(v) for v in value]
    return value


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "finvalda"


CENT = Decimal("0.01")


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def as_decimal(value: Any) -> str | None:
    """An amount from Finvalda (a JSON number or text) as an exact decimal string."""
    number = decimal_of(value)
    return None if number is None else format(number, "f")


_DOTNET_DATE = re.compile(r"^/Date\((-?\d+)([+-]\d{4})?\)/$")


@lru_cache(maxsize=1)
def _vilnius_zone() -> Any:
    """Europe/Vilnius from the time zone database, looked up once; None without one."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Europe/Vilnius")
    except Exception:  # noqa: BLE001 - no time zone data: the EU rule is used
        return None


def _zone_offset(moment: datetime) -> timedelta | None:
    """Europe/Vilnius's offset at ``moment``, when the runtime has time zone data."""
    zone = _vilnius_zone()
    return moment.astimezone(zone).utcoffset() if zone is not None else None


def _vilnius_offset(moment: datetime) -> timedelta:
    """Lithuania's offset at a UTC moment: EET +2, EEST +3 from the last Sunday of
    March 01:00 UTC to the last Sunday of October 01:00 UTC (EU rule)."""
    offset = _zone_offset(moment)
    if offset is not None:
        return offset

    def last_sunday(month: int) -> datetime:
        day = datetime(moment.year, month, 31, 1, tzinfo=timezone.utc)
        return day - timedelta(days=(day.weekday() + 1) % 7)

    summer = last_sunday(3) <= moment < last_sunday(10)
    return timedelta(hours=3 if summer else 2)


def _dotnet(text: str) -> datetime | None:
    """/Date(ms±hhmm)/ as local wall time: by its offset, else Lithuania's.

    Every other time from Finvalda (ISO text, since) is the server's local time,
    so a /Date/ without an offset is read as Lithuanian local time too.
    """
    match = _DOTNET_DATE.match(text)
    if not match:
        return None
    moment = datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)
    offset = match.group(2)
    if offset:
        sign = 1 if offset[0] == "+" else -1
        moment += sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))
    else:
        moment += _vilnius_offset(moment)
    return moment.replace(tzinfo=None)


_DATE_TIME = re.compile(
    r"^(\d{4})[-.](\d{2})[-.](\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,7}))?)?)?"
    r"\s*(Z|[+-]\d{2}:?\d{2})?"
)


def finvalda_datetime(value: Any) -> datetime | None:
    """A moment in one of Finvalda's forms (T or space, dashes or dots, /Date(ms)/)."""
    text = str(value or "").strip()
    match = _DATE_TIME.match(text)
    try:
        if match:
            year, month, day, hour, minute, second, fraction, zone = match.groups()
            micro = int((fraction or "0").ljust(6, "0")[:6])
            moment = datetime(
                int(year),
                int(month),
                int(day),
                int(hour or 0),
                int(minute or 0),
                int(second or 0),
                micro,
            )
            if zone:
                # A time with an explicit offset (one this extension returned, or a
                # workflow's): the same moment as Lithuanian wall time.
                if zone == "Z":
                    shift = timedelta(0)
                else:
                    digits = zone.replace(":", "")
                    sign = 1 if digits[0] == "+" else -1
                    shift = sign * timedelta(hours=int(digits[1:3]), minutes=int(digits[3:5]))
                utc = (moment - shift).replace(tzinfo=timezone.utc)
                return (utc + _vilnius_offset(utc)).replace(tzinfo=None)
            return moment
        return _dotnet(text)
    except (ValueError, OverflowError, OSError):
        return None
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def lithuanian_today() -> Date:
    """Today's date in Lithuania, whatever the time zone of the process."""
    now = _utc_now()
    return (now + _vilnius_offset(now)).date()


def finvalda_date(value: Any) -> Date | None:
    """A date in one of Finvalda's forms: 2026-11-10T00:00:00, 2026.11.10, /Date(ms+0300)/."""
    moment = finvalda_datetime(value)
    return moment.date() if moment else None


def iso_date(moment: datetime | None) -> str | None:
    """YYYY-MM-DD of a Lithuanian wall time."""
    return moment.date().isoformat() if moment is not None else None


def iso_moment(moment: datetime | None) -> str | None:
    """A Lithuanian wall time as ISO 8601 with its offset (+02:00 or +03:00)."""
    if moment is None:
        return None
    # The offset of a wall time: try the winter offset, then check it.
    guess = (moment - timedelta(hours=2)).replace(tzinfo=timezone.utc)
    offset = _vilnius_offset(guess)
    utc = (moment - offset).replace(tzinfo=timezone.utc)
    offset = _vilnius_offset(utc)
    return moment.replace(tzinfo=timezone(offset)).isoformat()
