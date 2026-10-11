"""Minimal BibTeX checks: enough to reject HTML or error pages, not a full parser."""

import re


class BibError(ValueError):
    pass


def records(text):
    """Return [(type, key, key_start, key_end, start, end)] for each entry in text."""
    bib = text.strip()
    out, i = [], 0
    while True:
        m = re.compile(r"@([a-z][a-z0-9_-]*)\s*\{", re.I).search(bib, i)
        if not m:
            break
        # Walk to the matching close brace; backslash escapes one character.
        depth, j, esc = 1, m.end(), False
        while j < len(bib) and depth:
            c = bib[j]
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            j += 1
        if depth:
            raise BibError("unbalanced braces in BibTeX")
        kind = m.group(1).lower()
        if kind not in ("comment", "preamble", "string"):
            km = re.compile(r"\s*([^,\s{}()]+)\s*,").match(bib, m.end())
            if not km:
                raise BibError("BibTeX entry has no citation key")
            out.append((kind, km.group(1), km.start(1), km.end(1), m.start(), j))
        i = j
    if not out:
        raise BibError("no BibTeX entry found")
    return out


def single(text):
    """Return stripped text if it holds exactly one entry with a title."""
    bib = text.strip()
    if len(records(bib)) != 1:
        raise BibError("expected exactly one BibTeX entry")
    if not field(bib, "title").strip("{} "):
        raise BibError("BibTeX entry has no title")
    return bib


def field(bib, name):
    """Value of a top-level field, outer braces or quotes removed; '' if absent."""
    m = re.search(r"[,\s]" + re.escape(name) + r"\s*=\s*", bib, re.I)
    if not m:
        return ""
    i, depth, quoted = m.end(), 0, False
    start = i
    while i < len(bib):
        c = bib[i]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                break
            depth -= 1
        elif c == '"' and depth == 0:
            quoted = not quoted
        elif c == "," and depth == 0 and not quoted:
            break
        i += 1
    value = bib[start:i].strip()
    if value[:1] in "{\"" and value[-1:] in "}\"":
        value = value[1:-1]
    return value.strip()


def key(bib):
    return records(bib)[0][1]


def set_key(bib, new):
    if not re.fullmatch(r"[\w:.+\-/]+", new):
        raise BibError("citation key may use letters, digits, _ : . + - /")
    _, _, s, e, _, _ = records(bib)[0]
    return bib[:s] + new + bib[e:]
