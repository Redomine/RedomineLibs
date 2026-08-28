# -*- coding: utf-8 -*-


LINE_TOLERANCE = 0.0001

COMMENT_SEPARATOR = u", "


try:
    text_type = unicode
except NameError:
    text_type = str


def safe_text(value):
    if value is None:
        return u""
    try:
        return text_type(value)
    except Exception:
        try:
            return text_type(value.ToString())
        except Exception:
            return u""


def comment_tokens(value):
    return [
        token.strip()
        for token in safe_text(value).split(u",")
        if token.strip()
    ]


def remove_comment_tags(value, tags):
    normalized_tags = set(
        safe_text(tag).strip().upper()
        for tag in tags
        if safe_text(tag).strip()
    )
    remaining = []
    removed_count = 0

    for token in comment_tokens(value):
        if token.upper() in normalized_tags:
            removed_count += 1
        else:
            remaining.append(token)

    return COMMENT_SEPARATOR.join(remaining), removed_count


def add_comment_tag(value, tag, prepend=False):
    normalized_tag = safe_text(tag).strip()
    cleaned_value, unused_removed_count = remove_comment_tags(
        value,
        (normalized_tag,),
    )
    tokens = comment_tokens(cleaned_value)

    if not normalized_tag:
        return COMMENT_SEPARATOR.join(tokens)
    if prepend:
        tokens.insert(0, normalized_tag)
    else:
        tokens.append(normalized_tag)
    return COMMENT_SEPARATOR.join(tokens)


def classify_line_orientation(
    start_z,
    end_z,
    direction_z,
    horizontal_value,
    vertical_value,
    sloped_value,
):
    if abs(float(start_z) - float(end_z)) < LINE_TOLERANCE:
        return horizontal_value
    if abs(abs(float(direction_z)) - 1.0) < LINE_TOLERANCE:
        return vertical_value
    return sloped_value
