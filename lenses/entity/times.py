# darqos
# Copyright (C) 2026 David Arnold

# Times as people type them, for the entity lens's time editor: a year,
# a month, or a day, written as "2024", "June 2024", "2024-06",
# "30 June 2024", "June 30, 2024" or "30/6/2024" (day first), a decade
# as "1970s", or an ISO 8601 time.  The precision is what was given.
# "about", "c." or "~" before it makes it approximate, ± 1 of its unit,
# and "BCE" or "BC" after it, before the common era.
#
# It uses no Qt, so it can be tested without a display.

import calendar
import re

import darq.kb as model

import render


P = model.TimePrecision

MONTHS = {name.lower(): n for n, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): n for n, name in enumerate(calendar.month_abbr) if name})
MONTHS["sept"] = 9

_APPROXIMATE = re.compile(r"^(?:about|approximately|approx\.?|circa|ca?\.|~)\s*", re.IGNORECASE)
_ERA = re.compile(r"\s*\b(BCE|BC|CE|AD)$", re.IGNORECASE)
_YEAR = r"(\d{1,9})"
_MONTH = r"([a-z]+)\.?"

# Each form, with the order of its fields: d(ay), m(onth name), n
# (month number), y(ear).
_FORMS = [
    (re.compile(rf"{_YEAR}"), "y"),
    (re.compile(rf"{_YEAR}s"), "decade"),
    (re.compile(rf"{_MONTH},?\s+{_YEAR}"), "my"),
    (re.compile(r"(\d{1,2})/" + _YEAR), "ny"),
    (re.compile(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH},?\s+{_YEAR}"), "dmy"),
    (re.compile(rf"{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+{_YEAR}"), "mdy"),
    (re.compile(r"(\d{1,2})[/.](\d{1,2})[/.]" + _YEAR), "dny"),
]


def parse(text: str) -> model.TimeValue:
    """Return the time some text describes.

    :raises ValueError: if it doesn't describe one, with why."""
    text = " ".join(text.split())
    if not text:
        raise ValueError("no time given")

    approximate = _APPROXIMATE.match(text)
    if approximate:
        text = text[approximate.end():]
    bce = False
    era = _ERA.search(text)
    if era:
        bce = era.group(1).upper() in ("BCE", "BC")
        text = text[:era.start()]

    time = _parse(text.lower())
    if bce:
        if time.year <= 0:
            raise ValueError("a year before the common era is from 1")
        time.year = -time.year
    if approximate:
        time.before = time.after = 1
    return time


def text(time: model.TimeValue) -> str:
    """Return a time as it's edited: as it's shown, which parse() reads
    back, for a year, month or day, or a decade, approximate or not."""
    if time.before == time.after == 1:
        exact = model.TimeValue(time.year, time.month, time.day, time.hour, time.minute, time.second,
                                time.nanosecond, time.precision, calendar=time.calendar)
        return f"about {render.format_time(exact)}"
    return render.format_time(time)


def _parse(text: str) -> model.TimeValue:
    for pattern, order in _FORMS:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        fields = match.groups()
        if order == "decade":
            year = int(fields[0])
            if year % 10:
                raise ValueError(f"{year}s isn't a decade")
            return model.TimeValue(year, precision=P.TenYears)

        year = month = day = None
        for kind, field in zip(order, fields):
            if kind == "y":
                year = int(field)
            elif kind == "n":
                month = int(field)
            elif kind == "d":
                day = int(field)
            elif kind == "m":
                month = MONTHS.get(field)
                if month is None:
                    raise ValueError(f"{field!r} isn't a month")
        assert year is not None
        return _time(year, month, day)

    try:
        return model.TimeValue.parse(text.upper())
    except ValueError:
        raise ValueError(f"can't read {text!r} as a time: try 2024, June 2024 or 30 June 2024") from None


def _time(year: int, month: int | None, day: int | None) -> model.TimeValue:
    if month is None:
        return model.TimeValue(year, precision=P.Years)
    if not 1 <= month <= 12:
        raise ValueError(f"there's no month {month}")
    if day is None:
        return model.TimeValue(year, month, precision=P.Months)
    days = calendar.monthrange(year, month)[1] if year >= 1 else 31
    if not 1 <= day <= days:
        raise ValueError(f"{calendar.month_name[month]} {year} has no day {day}")
    return model.TimeValue(year, month, day, precision=P.Days)
