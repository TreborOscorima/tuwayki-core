from __future__ import annotations

import datetime

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

DEFAULT_TZ = "UTC"
UTC = datetime.timezone.utc


def _country_config(country_code: str | None) -> dict:
    from tuwayki_core.countries import get_country_config

    return get_country_config(country_code or "PE")


def _resolve_timezone_key(
    country_code: str | None,
    timezone: str | None = None,
) -> str:
    tz_key = (timezone or "").strip()
    if tz_key:
        return tz_key
    config = _country_config(country_code)
    tz_key = config.get("timezone") or DEFAULT_TZ
    return tz_key


def _resolve_zoneinfo(
    country_code: str | None,
    timezone: str | None = None,
):
    if ZoneInfo is None:
        return None
    tz_key = _resolve_timezone_key(country_code, timezone)
    try:
        return ZoneInfo(tz_key)
    except Exception:
        return None


def is_valid_timezone(timezone: str | None) -> bool:
    tz_key = (timezone or "").strip()
    if not tz_key:
        return True
    if ZoneInfo is None:
        return True
    try:
        ZoneInfo(tz_key)
        return True
    except Exception:
        return False


def country_now(
    country_code: str | None,
    timezone: str | None = None,
) -> datetime.datetime:
    tz_key = _resolve_timezone_key(country_code, timezone)
    if ZoneInfo is not None:
        try:
            return datetime.datetime.now(ZoneInfo(tz_key))
        except Exception:
            pass
    return datetime.datetime.now()


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(UTC)


def utc_now_naive() -> datetime.datetime:
    return utc_now().replace(tzinfo=None)


def country_today_date(
    country_code: str | None,
    timezone: str | None = None,
) -> datetime.date:
    return country_now(country_code, timezone=timezone).date()


def country_today_start(
    country_code: str | None,
    timezone: str | None = None,
) -> datetime.datetime:
    now = country_now(country_code, timezone=timezone)
    return now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def to_local_datetime(
    value: datetime.datetime | None,
    country_code: str | None,
    timezone: str | None = None,
    *,
    assume_utc_naive: bool = True,
) -> datetime.datetime | None:
    if value is None:
        return None
    zone = _resolve_zoneinfo(country_code, timezone)
    if zone is None:
        return value

    current = value
    if current.tzinfo is None:
        source_zone = UTC if assume_utc_naive else zone
        current = current.replace(tzinfo=source_zone)
    return current.astimezone(zone)


def format_local_datetime(
    value: datetime.datetime | None,
    fmt: str,
    country_code: str | None,
    timezone: str | None = None,
    *,
    assume_utc_naive: bool = True,
    empty: str = "",
) -> str:
    localized = to_local_datetime(
        value,
        country_code,
        timezone=timezone,
        assume_utc_naive=assume_utc_naive,
    )
    if localized is None:
        return empty
    return localized.strftime(fmt)


def local_datetime_to_utc_naive(
    value: datetime.datetime,
    country_code: str | None,
    timezone: str | None = None,
) -> datetime.datetime:
    zone = _resolve_zoneinfo(country_code, timezone)
    if zone is None:
        return value.replace(tzinfo=None) if value.tzinfo else value

    current = value
    if current.tzinfo is None:
        current = current.replace(tzinfo=zone)
    return current.astimezone(UTC).replace(tzinfo=None)


def _coerce_day_value(
    day_value: str | datetime.date | datetime.datetime | None,
    country_code: str | None,
    timezone: str | None = None,
) -> datetime.date:
    if day_value is None:
        return country_today_date(country_code, timezone=timezone)
    if isinstance(day_value, datetime.datetime):
        return day_value.date()
    if isinstance(day_value, datetime.date):
        return day_value
    if isinstance(day_value, str):
        return datetime.datetime.strptime(day_value, "%Y-%m-%d").date()
    raise ValueError("Formato de fecha no soportado.")


def local_day_bounds_utc_naive(
    day_value: str | datetime.date | datetime.datetime | None,
    country_code: str | None,
    timezone: str | None = None,
) -> tuple[datetime.datetime, datetime.datetime]:
    target_day = _coerce_day_value(day_value, country_code, timezone=timezone)
    local_start = datetime.datetime.combine(target_day, datetime.time.min)
    local_end = datetime.datetime.combine(target_day, datetime.time.max)
    return (
        local_datetime_to_utc_naive(local_start, country_code, timezone=timezone),
        local_datetime_to_utc_naive(local_end, country_code, timezone=timezone),
    )
