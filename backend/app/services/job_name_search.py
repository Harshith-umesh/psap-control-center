"""Bounded regex validation for FournosJob-name autocomplete searches."""

from __future__ import annotations

import re


MAX_PATTERN_LENGTH = 128


def compile_job_name_pattern(pattern: str) -> re.Pattern[str]:
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(f"Search patterns must be {MAX_PATTERN_LENGTH} characters or fewer")
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Invalid regular expression: {exc}") from exc

    # Permit useful name searches such as `mi355x.*jql`, but prevent nested or
    # excessive repetition from making the backtracking regex engine expensive.
    in_character_class = False
    escaped = False
    quantifier_count = 0
    unbounded_count = 0
    for index, char in enumerate(pattern):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "[":
            in_character_class = True
            continue
        if char == "]" and in_character_class:
            in_character_class = False
            continue
        if in_character_class:
            continue
        if char == ")" and index + 1 < len(pattern) and pattern[index + 1] in "*+?{":
            raise ValueError("Repeating a regex group is not supported for job-name search")
        if char in "*+?":
            if char == "?" and index > 0 and pattern[index - 1] in "*+?}":
                continue  # a lazy suffix
            if char == "?" and index > 0 and pattern[index - 1] == "(":
                continue  # non-capturing/lookaround group modifier
            quantifier_count += 1
            if char in "*+":
                unbounded_count += 1
        elif char == "{":
            end = pattern.find("}", index + 1)
            if end != -1 and re.fullmatch(r"\d+(?:,\d*)?", pattern[index + 1:end]):
                quantifier_count += 1
                if pattern[index + 1:end].endswith(","):
                    unbounded_count += 1
    if quantifier_count > 6 or unbounded_count > 2:
        raise ValueError("Regex is too complex; simplify the job-name search pattern")
    return compiled
