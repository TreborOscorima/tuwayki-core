"""Pure date/time utility functions."""
import datetime


def get_today_str() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")


def get_current_month_str() -> str:
    return datetime.date.today().strftime("%Y-%m")


def get_current_week_str() -> str:
    return datetime.date.today().strftime("%G-W%V")
