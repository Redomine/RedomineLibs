# -*- coding: utf-8 -*-
from __future__ import division

import math


try:
    text_type = unicode
except NameError:
    text_type = str


class NumericValueError(ValueError):
    pass


def _as_text(value):
    if value is None:
        return u""
    try:
        return text_type(value)
    except Exception:
        return text_type(str(value))


def _normalize_repeated_separator(mantissa, separator):
    parts = mantissa.split(separator)
    if len(parts) <= 2:
        return mantissa.replace(separator, ".")

    if all(len(part) == 3 for part in parts[1:]):
        return u"".join(parts)

    return u"".join(parts[:-1]) + u"." + parts[-1]


def _normalize_number_text(value):
    text = _as_text(value).strip()
    text = text.replace(u"\u2212", u"-")
    text = text.replace(u"\xa0", u"")
    text = text.replace(u"\u202f", u"")
    text = text.replace(u" ", u"")
    if not text:
        raise NumericValueError(u"пустое значение")

    exponent_index = max(text.rfind(u"e"), text.rfind(u"E"))
    if exponent_index > 0:
        mantissa = text[:exponent_index]
        exponent = text[exponent_index:]
    else:
        mantissa = text
        exponent = u""

    comma_index = mantissa.rfind(u",")
    dot_index = mantissa.rfind(u".")
    if comma_index >= 0 and dot_index >= 0:
        if comma_index > dot_index:
            mantissa = mantissa.replace(u".", u"").replace(u",", u".")
        else:
            mantissa = mantissa.replace(u",", u"")
    elif comma_index >= 0:
        mantissa = _normalize_repeated_separator(mantissa, u",")
    elif mantissa.count(u".") > 1:
        mantissa = _normalize_repeated_separator(mantissa, u".")

    return mantissa + exponent


def parse_number(value):
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        normalized = _normalize_number_text(value)
        try:
            number = float(normalized)
        except (TypeError, ValueError):
            raise NumericValueError(
                u"некорректное число: {0}".format(_as_text(value))
            )

    if math.isnan(number) or math.isinf(number):
        raise NumericValueError(u"число должно быть конечным")
    return number


def round_half_away_from_zero(value):
    number = parse_number(value)
    if number >= 0:
        return int(math.floor(number + 0.5))
    return int(math.ceil(number - 0.5))


def format_number(value):
    number = parse_number(value)
    result = u"{0:.15g}".format(number)
    if result in (u"-0", u"-0.0"):
        return u"0"
    return result
