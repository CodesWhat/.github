#!/usr/bin/env python3
"""Select the target-result artifacts produced by the current workflow attempt."""

import argparse
from datetime import datetime, timezone
import functools
import json
import re
import struct
import subprocess
import sys
import unicodedata

PAGE_SIZE = 100
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


class SelectionError(Exception):
    pass


# The matcher below mirrors minimatch 10 with default options, which is what
# actions/download-artifact applies to its `pattern` input. Artifact names are
# a single path segment, so alternatives that contain a path separator can
# never match, and the globstar and ".." forms of them are refused rather than
# guessed at.

_BRACE_ESCAPES = [
    ("\\\\", "\0SLASH\0"),
    ("\\{", "\0OPEN\0"),
    ("\\}", "\0CLOSE\0"),
    ("\\,", "\0COMMA\0"),
    ("\\.", "\0PERIOD\0"),
]
_BRACE_UNESCAPES = [
    ("\0SLASH\0", "\\"),
    ("\0OPEN\0", "{"),
    ("\0CLOSE\0", "}"),
    ("\0COMMA\0", ","),
    ("\0PERIOD\0", "."),
]
_ESC_CLOSE = "\0CLOSE\0"
_LINE_END = "\n\r\u2028\u2029"
_BRACE_GROUP = re.compile("\\{(?:(?!\\{)[^" + _LINE_END + "])*\\}")
_COMMA_SET = re.compile(",(?!,)[^" + _LINE_END + "]*\\}")
_NUMERIC_SEQUENCE = re.compile(r"-?[0-9]+\.\.-?[0-9]+(?:\.\.-?[0-9]+)?")
_ALPHA_SEQUENCE = re.compile(r"[a-zA-Z]\.\.[a-zA-Z](?:\.\.-?[0-9]+)?")
_PADDED = re.compile(r"-?0[0-9]")
_NUMBER = re.compile(r"-?[0-9]+")
BRACE_MAX = 100000
BRACE_MAX_LENGTH = 4000000
BRACE_MAX_DEPTH = 200
BRACE_MAX_DIGITS = 4000


def _brace_range(text):
    """Return the first balanced {..} pair the way balanced-match does."""
    start = text.find("{")
    end = text.find("}", start + 1)
    if start < 0 or end <= 0:
        return None
    begs = []
    left = len(text)
    right = None
    result = None
    i = start
    while i >= 0 and result is None:
        if i == start:
            begs.append(i)
            start = text.find("{", i + 1)
        elif len(begs) == 1:
            result = (begs.pop(), end)
        else:
            beg = begs.pop() if begs else None
            if beg is not None and beg < left:
                left = beg
                right = end
            end = text.find("}", i + 1)
        i = start if (start < end and start >= 0) else end
    if begs and right is not None:
        result = (left, right)
    return result


def _brace_capped():
    return SelectionError(
        "brace expansion in artifact pattern exceeds its size limit and would drop alternatives"
    )


def _brace_combine(acc, prefix_text, values, drop_empties):
    out = []
    length = 0
    nonempty = None
    for head in acc:
        prefix = head + prefix_text
        candidates = values
        if drop_empties and not prefix:
            if nonempty is None:
                nonempty = [value for value in values if value]
            candidates = nonempty
        for value in candidates:
            if len(out) >= BRACE_MAX:
                raise _brace_capped()
            expansion = prefix + value
            if length + len(expansion) > BRACE_MAX_LENGTH:
                raise _brace_capped()
            out.append(expansion)
            length += len(expansion)
    return out


def _brace_numeric(text):
    if not _NUMBER.fullmatch(text):
        return ord(text[0])
    if len(text) > BRACE_MAX_DIGITS:
        raise SelectionError("brace range bound in artifact pattern is too large")
    return int(text, 10)


def _brace_sequence(body, alpha):
    parts = body.split("..")
    start = _brace_numeric(parts[0])
    stop = _brace_numeric(parts[1])
    width = max(len(parts[0]), len(parts[1]))
    step = max(abs(_brace_numeric(parts[2])), 1) if len(parts) == 3 else 1
    reverse = stop < start
    if reverse:
        step = -step
    pad = any(_PADDED.match(part) for part in parts)
    values = []
    length = 0
    i = start
    while i >= stop if reverse else i <= stop:
        if len(values) >= BRACE_MAX:
            raise _brace_capped()
        if alpha:
            value = chr(i)
            if value == "\\":
                value = ""
        else:
            value = str(i)
            if pad:
                need = width - len(value)
                if need > 0:
                    zeros = "0" * need
                    value = "-" + zeros + value[1:] if i < 0 else zeros + value
        if length + len(value) > BRACE_MAX_LENGTH:
            raise _brace_capped()
        values.append(value)
        length += len(value)
        i += step
    return values


def _brace_comma_parts(body):
    """Split on commas, keeping nested {..} groups whole."""
    parts = []
    carry = ""
    while True:
        found = _brace_range(body)
        if found is None:
            tail = body.split(",")
            tail[0] = carry + tail[0]
            parts.extend(tail)
            return parts
        pre = body[:found[0]]
        inner = body[found[0] + 1:found[1]]
        post = body[found[1] + 1:]
        head = pre.split(",")
        head[0] = carry + head[0]
        head[-1] += "{" + inner + "}"
        if not post:
            parts.extend(head)
            return parts
        carry = head.pop()
        parts.extend(head)
        body = post


def _brace_expand_inner(text, depth, is_top):
    if depth > BRACE_MAX_DEPTH:
        raise SelectionError("brace nesting is too deep in artifact pattern")
    acc = [""]
    drop_empties = False
    first_group = True
    rewrites = 0
    while True:
        found = _brace_range(text)
        if found is None:
            return _brace_combine(acc, text, [""], drop_empties)
        pre = text[:found[0]]
        body = text[found[0] + 1:found[1]]
        post = text[found[1] + 1:]
        literal = pre + "{" + body + "}" + post
        if pre.endswith("$"):
            acc = _brace_combine(
                acc, pre + "{" + body + "}", [""], drop_empties and not post
            )
            first_group = False
            if not post:
                break
            text = post
            continue
        numeric = _NUMERIC_SEQUENCE.fullmatch(body) is not None
        alpha = _ALPHA_SEQUENCE.fullmatch(body) is not None
        sequence = numeric or alpha
        options = "," in body
        if not sequence and not options:
            if rewrites < 1000 and _COMMA_SET.search(post):
                rewrites += 1
                text = pre + "{" + body + _ESC_CLOSE + post
                is_top = True
                continue
            return _brace_combine(acc, literal, [""], drop_empties)
        if first_group:
            drop_empties = is_top and not sequence
            first_group = False
        if sequence:
            values = _brace_sequence(body, alpha)
        else:
            members = _brace_comma_parts(body)
            if len(members) == 1:
                members = [
                    "{" + item + "}"
                    for item in _brace_expand_inner(members[0], depth + 1, False)
                ]
                if len(members) == 1:
                    acc = _brace_combine(
                        acc, pre + members[0], [""], drop_empties and not post
                    )
                    if not post:
                        break
                    text = post
                    continue
            drops = drop_empties and not post and not pre and not any(acc)
            values = []
            length = 0
            for member in members:
                for value in _brace_expand_inner(member, depth + 1, False):
                    if drops and not value:
                        continue
                    if len(values) >= BRACE_MAX or length + len(value) > BRACE_MAX_LENGTH:
                        raise _brace_capped()
                    values.append(value)
                    length += len(value)
        acc = _brace_combine(acc, pre, values, drop_empties and not post)
        if not post:
            break
        text = post
    return acc


def expand_braces(pattern):
    """Expand {a,b} and {1..3} alternatives the way minimatch does.

    Matches brace-expansion as bundled with minimatch 10, including its quirks:
    a group without a comma or range stays literal, a leading {} is literal,
    and backslash escapes of braces, commas and dots are consumed here.
    """
    if not _BRACE_GROUP.search(pattern):
        return [pattern]
    text = pattern
    if text[:2] == "{}":
        text = "\\{\\}" + text[2:]
    for plain, sentinel in _BRACE_ESCAPES:
        text = text.replace(plain, sentinel)
    expanded = []
    seen = set()
    for item in _brace_expand_inner(text, 0, True):
        for sentinel, plain in _BRACE_UNESCAPES:
            item = item.replace(sentinel, plain)
        if item not in seen:
            seen.add(item)
            expanded.append(item)
    return expanded


_EXTGLOB_TYPES = "!?+*@"
_ADOPT_ANY = {"!": "?@", "?": "?@", "@": "?@", "*": "*+?@", "+": "+@?*"}
_ADOPT = {"!": "@", "?": "?@", "@": "@", "*": "*+?@", "+": "+@"}
_ADOPT_SPACE = {"!": "?", "@": "?", "+": "?*"}
_USURP = {
    "!": {"!": "@"},
    "?": {"*": "*", "+": "*"},
    "@": {"!": "!", "?": "?", "@": "@", "*": "*", "+": "+"},
    "+": {"?": "*", "*": "*"},
}
_RE_SPECIALS = "().*{}+?[]^$\\!"
_RE_ESCAPED = set("-[]{}()*+?.,\\^$|#") | set(" \t\n\r\v\f\u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff") | {
    chr(code) for code in range(0x2000, 0x200b)
}
_CLASS_ESCAPED = set("[]\\-&~|")
_START_NO_DOT = "(?!\\.)"
_START_NO_TRAVERSAL = "(?!(?:^|/)\\.\\.?(?:\\Z|/))"
_QMARK = "[^/]"
_STAR = _QMARK + "*?"
_STAR_NO_EMPTY = _QMARK + "+?"
_NEVER = "(?!)"
_STAR_ONLY = re.compile(r"\*+")
_STAR_EXT = re.compile(r"\*+([^+@!?*\[(]*)")
_QMARKS = re.compile(r"\?+([^+@!?*\[(]*)?")
_STAR_DOT_STAR = re.compile(r"\*+\.\*+")
_DOT_STAR = re.compile(r"\.\*+")
_MAX_PATTERN_LENGTH = 65536
_MAX_EXTGLOB_RECURSION = 2
_MAX_PARSE_NESTING = 64
_MAX_AST_WORK = 30000


class _Budget(object):
    """Cumulative count of syntax-tree nodes created, copied or rendered while
    compiling one pattern. Each !(...) copies its later siblings, so chained
    negations grow exponentially; the budget fails that before it is paid."""

    def __init__(self, limit=_MAX_AST_WORK):
        self.remaining = limit

    def spend(self, amount=1):
        self.remaining -= amount
        if self.remaining < 0:
            raise SelectionError("extglob expansion is too large in artifact pattern")


def _re_escape(char):
    return "\\" + char if char in _RE_ESCAPED else char


def _unescape(text):
    text = re.sub(
        r"((?!\\)[^" + _LINE_END + r"]|^)\[([^/\\])\]", r"\1\2", text
    )
    return re.sub(r"\\([^/])", r"\1", text)


def _utf16_units(text):
    """Re-express a string as UTF-16 code units, which is how a JS RegExp
    without the u flag sees it (so ? matches half of an astral character)."""
    if all(ord(char) < 0x10000 for char in text):
        return text
    raw = text.encode("utf-16-le", "surrogatepass")
    return "".join(chr(unit) for unit in struct.unpack("<%dH" % (len(raw) // 2), raw))


# posix class -> (unicode general categories, extra characters, negated)
_POSIX_CLASSES = [
    ("[:alnum:]", ("L", "Nl", "Nd"), "", False),
    ("[:alpha:]", ("L", "Nl"), "", False),
    ("[:ascii:]", None, "\\x00-\\x7f", False),
    ("[:blank:]", ("Zs",), "\\t", False),
    ("[:cntrl:]", ("Cc",), "", False),
    ("[:digit:]", ("Nd",), "", False),
    ("[:graph:]", ("Z", "C"), "", True),
    ("[:lower:]", ("Ll",), "", False),
    ("[:print:]", ("C",), "", False),
    ("[:punct:]", ("P",), "", False),
    ("[:space:]", ("Z",), "\\t\\r\\n\\x0b\\x0c", False),
    ("[:upper:]", ("Lu",), "", False),
    ("[:word:]", ("L", "Nl", "Nd", "Pc"), "", False),
    ("[:xdigit:]", None, "A-Fa-f0-9", False),
]


@functools.lru_cache(maxsize=None)
def _category_class_body(categories):
    """Character-class body for the given unicode general categories."""
    ranges = []
    first = last = None
    for code in range(0x110000):
        category = unicodedata.category(chr(code))
        if any(category.startswith(wanted) for wanted in categories):
            if first is None:
                first = code
            last = code
            continue
        if first is not None:
            ranges.append((first, last))
            first = None
    if first is not None:
        ranges.append((first, last))
    return "".join(
        "\\U%08x" % low if low == high else "\\U%08x-\\U%08x" % (low, high)
        for low, high in ranges
    )


def _class_escape(char):
    return "\\" + char if char in _CLASS_ESCAPED else char


def _parse_class(glob, position):
    """Translate a [...] class. Returns (source, needs_unicode, consumed, magic)."""
    ranges = []
    negs = []
    i = position + 1
    saw_start = False
    uflag = False
    escaping = False
    negate = False
    end_pos = position
    range_start = ""
    while i < len(glob):
        char = glob[i]
        if char in "!^" and i == position + 1:
            negate = True
            i += 1
            continue
        if char == "]" and saw_start and not escaping:
            end_pos = i + 1
            break
        saw_start = True
        if char == "\\":
            if not escaping:
                escaping = True
                i += 1
                continue
        if char == "[" and not escaping:
            matched = False
            for name, categories, extra, neg in _POSIX_CLASSES:
                if glob.startswith(name, i):
                    if range_start:
                        return _NEVER, False, len(glob) - position, True
                    i += len(name)
                    body = extra
                    if categories:
                        body = _category_class_body(categories) + extra
                        uflag = True
                    (negs if neg else ranges).append(body)
                    matched = True
                    break
            if matched:
                continue
        escaping = False
        if range_start:
            if char > range_start:
                ranges.append(_class_escape(range_start) + "-" + _class_escape(char))
            elif char == range_start:
                ranges.append(_class_escape(char))
            range_start = ""
            i += 1
            continue
        if glob.startswith("-]", i + 1):
            ranges.append(_class_escape(char) + "\\-")
            i += 2
            continue
        if glob.startswith("-", i + 1):
            range_start = char
            i += 2
            continue
        ranges.append(_class_escape(char))
        i += 1
    if end_pos < i:
        return "", False, 0, False
    if not ranges and not negs:
        return _NEVER, False, len(glob) - position, True
    if (
        not negs
        and len(ranges) == 1
        and not negate
        and (len(ranges[0]) == 1 or (len(ranges[0]) == 2 and ranges[0][0] == "\\"))
    ):
        literal = ranges[0][-1]
        return _re_escape(literal), False, end_pos - position, False
    positive = "[" + ("^" if negate else "") + "".join(ranges) + "]"
    negative = "[" + ("" if negate else "^") + "".join(negs) + "]"
    if ranges and negs:
        combined = "(?:" + positive + "|" + negative + ")"
    elif ranges:
        combined = positive
    else:
        combined = negative
    return combined, uflag, end_pos - position, True


def _parse_glob(glob, no_empty):
    """Translate one literal run of a pattern. Returns (source, uflag, magic)."""
    escaping = False
    source = ""
    uflag = False
    magic = False
    in_star = False
    i = 0
    while i < len(glob):
        char = glob[i]
        if escaping:
            escaping = False
            if char == "|":
                raise SelectionError(
                    "unsupported escaped pipe in artifact pattern: minimatch turns it into regex alternation"
                )
            source += ("\\" if char in _RE_SPECIALS else "") + char
            i += 1
            continue
        if char == "*":
            if not in_star:
                in_star = True
                source += _STAR_NO_EMPTY if no_empty and _STAR_ONLY.fullmatch(glob) else _STAR
                magic = True
            i += 1
            continue
        in_star = False
        if char == "\\":
            if i == len(glob) - 1:
                source += "\\\\"
            else:
                escaping = True
            i += 1
            continue
        if char == "[":
            translated, needs_unicode, consumed, class_magic = _parse_class(glob, i)
            if consumed:
                source += translated
                uflag = uflag or needs_unicode
                magic = magic or class_magic
                i += consumed
                continue
        if char == "?":
            source += _QMARK
            magic = True
            i += 1
            continue
        source += _re_escape(char)
        i += 1
    return source, uflag, magic


class _Ast(object):
    """Extglob syntax tree, following minimatch's AST."""

    def __init__(self, kind, parent, budget=None):
        self.type = kind
        self.parent = parent
        self.root = parent.root if parent is not None else self
        self.budget = parent.budget if parent is not None else (budget or _Budget())
        self.budget.spend()
        self.parts = []
        self.empty_ext = False
        self.has_magic = True if kind else None
        self.filled_negs = False
        self.negs = [] if self.root is self else self.root.negs
        if kind == "!" and not self.root.filled_negs:
            self.negs.append(self)
        self.parent_index = len(parent.parts) if parent is not None else 0

    def push(self, *parts):
        for part in parts:
            if part == "":
                continue
            self.parts.append(part)

    def to_string(self):
        if not self.type:
            return "".join(str(part) if isinstance(part, str) else part.to_string() for part in self.parts)
        return (
            self.type
            + "("
            + "|".join(part.to_string() for part in self.parts)
            + ")"
        )

    def can_adopt_type(self, kind, table=_ADOPT_ANY):
        return kind in table.get(self.type, "")

    def can_adopt(self, child, table=_ADOPT):
        if (
            not isinstance(child, _Ast)
            or child.type is not None
            or len(child.parts) != 1
            or self.type is None
        ):
            return False
        grandchild = child.parts[0]
        if not isinstance(grandchild, _Ast) or grandchild.type is None:
            return False
        return self.can_adopt_type(grandchild.type, table)

    def adopt(self, child, index):
        grandchild = child.parts[0]
        self.parts[index:index + 1] = grandchild.parts
        for part in grandchild.parts:
            if isinstance(part, _Ast):
                part.parent = self

    def adopt_with_space(self, child, index):
        grandchild = child.parts[0]
        blank = _Ast(None, grandchild)
        blank.parts.append("")
        grandchild.push(blank)
        self.adopt(child, index)

    def can_usurp(self, child):
        if (
            not isinstance(child, _Ast)
            or child.type is not None
            or len(child.parts) != 1
            or self.type is None
            or len(self.parts) != 1
        ):
            return False
        grandchild = child.parts[0]
        if not isinstance(grandchild, _Ast) or grandchild.type is None:
            return False
        return grandchild.type in _USURP.get(self.type, {})

    def usurp(self, child):
        grandchild = child.parts[0]
        self.parts = grandchild.parts
        for part in self.parts:
            if isinstance(part, _Ast):
                part.parent = self
        self.type = _USURP[self.type][grandchild.type]
        self.empty_ext = False

    def flatten(self):
        if not self.type:
            for part in self.parts:
                if isinstance(part, _Ast):
                    part.flatten()
            return
        for _ in range(10):
            done = True
            index = 0
            while index < len(self.parts):
                child = self.parts[index]
                if isinstance(child, _Ast):
                    child.flatten()
                    if self.can_adopt(child):
                        done = False
                        self.adopt(child, index)
                    elif self.can_adopt(child, _ADOPT_SPACE):
                        done = False
                        self.adopt_with_space(child, index)
                    elif self.can_usurp(child):
                        done = False
                        self.usurp(child)
                index += 1
            if done:
                break

    def is_start(self):
        if self.root is self:
            return True
        if self.parent is None or not self.parent.is_start():
            return False
        if self.parent_index == 0:
            return True
        for index in range(self.parent_index):
            sibling = self.parent.parts[index] if index < len(self.parent.parts) else None
            if not (isinstance(sibling, _Ast) and sibling.type == "!"):
                return False
        return True

    def is_end(self):
        if self.root is self:
            return True
        if self.parent is not None and self.parent.type == "!":
            return True
        if self.parent is None or not self.parent.is_end():
            return False
        if not self.type:
            return self.parent.is_end()
        return self.parent_index == len(self.parent.parts) - 1

    def copy_in(self, part):
        if isinstance(part, str):
            self.budget.spend()
            self.push(part)
        else:
            self.push(part.clone(self))

    def clone(self, parent):
        copy = _Ast(self.type, parent)
        for part in self.parts:
            copy.copy_in(part)
        return copy

    def fill_negs(self):
        if self.filled_negs:
            return
        self.to_string()
        self.filled_negs = True
        while self.negs:
            neg = self.negs.pop()
            if neg.type != "!":
                continue
            child = neg
            ancestor = child.parent
            while ancestor is not None:
                index = child.parent_index + 1
                while not ancestor.type and index < len(ancestor.parts):
                    for part in neg.parts:
                        part.copy_in(ancestor.parts[index])
                    index += 1
                child = ancestor
                ancestor = child.parent

    def parts_to_re(self, dot):
        pieces = []
        uflag = False
        for part in self.parts:
            source, part_uflag, _ = part.to_re(dot)
            uflag = uflag or part_uflag
            pieces.append(source)
        keep_empty = not (self.is_start() and self.is_end())
        return "|".join(piece for piece in pieces if keep_empty or piece), uflag

    def to_re(self, allow_dot=None):
        """Return (regexp source, needs unicode flag, has magic)."""
        dot = bool(allow_dot)
        self.budget.spend()
        if self.root is self:
            self.flatten()
            self.fill_negs()
        if not self.type:
            no_empty = (
                self.is_start()
                and self.is_end()
                and all(isinstance(part, str) for part in self.parts)
            )
            pieces = []
            uflag = False
            for part in self.parts:
                if isinstance(part, str):
                    source, part_uflag, magic = _parse_glob(part, no_empty)
                else:
                    source, part_uflag, magic = part.to_re(allow_dot)
                self.has_magic = bool(self.has_magic) or magic
                uflag = uflag or part_uflag
                pieces.append(source)
            source = "".join(pieces)
            start = ""
            if self.is_start() and self.parts and isinstance(self.parts[0], str):
                if not (len(self.parts) == 1 and self.parts[0] in ("..", ".")):
                    first = source[:1]
                    need_no_traversal = (
                        (dot and first != "" and first in "[.")
                        or (source.startswith("\\.") and source[2:3] != "" and source[2:3] in "[.")
                        or (source.startswith("\\.\\.") and source[4:5] != "" and source[4:5] in "[.")
                    )
                    need_no_dot = not dot and first != "" and first in "[."
                    start = (
                        _START_NO_TRAVERSAL
                        if need_no_traversal
                        else _START_NO_DOT if need_no_dot else ""
                    )
            end = ""
            if (
                self.is_end()
                and self.root.filled_negs
                and self.parent is not None
                and self.parent.type == "!"
            ):
                end = "(?:\\Z|/)"
            self.has_magic = bool(self.has_magic)
            return start + source + end, uflag, self.has_magic
        repeated = self.type in "*+"
        start = "(?:(?!(?:" if self.type == "!" else "(?:"
        body, uflag = self.parts_to_re(dot)
        if self.is_start() and self.is_end() and not body and self.type != "!":
            literal = self.to_string()
            self.parts = [literal]
            self.type = None
            self.has_magic = None
            return literal, False, False
        body_dot_allowed = ""
        if repeated and not allow_dot and not dot:
            body_dot_allowed, dot_uflag = self.parts_to_re(True)
            uflag = uflag or dot_uflag
        if body_dot_allowed == body:
            body_dot_allowed = ""
        if body_dot_allowed:
            body = "(?:" + body + ")(?:" + body_dot_allowed + ")*?"
        if self.type == "!" and self.empty_ext:
            final = (_START_NO_DOT if self.is_start() and not dot else "") + _STAR_NO_EMPTY
        else:
            if self.type == "!":
                close = (
                    "))"
                    + (_START_NO_DOT if self.is_start() and not dot and not allow_dot else "")
                    + _STAR
                    + ")"
                )
            elif self.type == "@":
                close = ")"
            elif self.type == "?":
                close = ")?"
            elif self.type == "+" and body_dot_allowed:
                close = ")"
            elif self.type == "*" and body_dot_allowed:
                close = ")?"
            else:
                close = ")" + self.type
            final = start + body + close
        self.has_magic = True
        return final, uflag, True


def _parse_ast(text, ast, pos, ext_depth, nesting=0):
    if nesting > _MAX_PARSE_NESTING:
        raise SelectionError("extglob nesting is too deep in artifact pattern")
    escaping = False
    in_brace = False
    brace_start = -1
    brace_neg = False
    if ast.type is None:
        i = pos
        acc = ""
        while i < len(text):
            char = text[i]
            i += 1
            if escaping or char == "\\":
                escaping = not escaping
                acc += char
                continue
            if in_brace:
                if i == brace_start + 1:
                    if char in "^!":
                        brace_neg = True
                elif char == "]" and not (i == brace_start + 2 and brace_neg):
                    in_brace = False
                acc += char
                continue
            elif char == "[":
                in_brace = True
                brace_start = i
                brace_neg = False
                acc += char
                continue
            if (
                char in _EXTGLOB_TYPES
                and text[i:i + 1] == "("
                and ext_depth <= _MAX_EXTGLOB_RECURSION
            ):
                ast.push(acc)
                acc = ""
                ext = _Ast(char, ast)
                i = _parse_ast(text, ext, i, ext_depth + 1, nesting + 1)
                ast.push(ext)
                continue
            acc += char
        ast.push(acc)
        return i
    i = pos + 1
    part = _Ast(None, ast)
    parts = []
    acc = ""
    while i < len(text):
        char = text[i]
        i += 1
        if escaping or char == "\\":
            escaping = not escaping
            acc += char
            continue
        if in_brace:
            if i == brace_start + 1:
                if char in "^!":
                    brace_neg = True
            elif char == "]" and not (i == brace_start + 2 and brace_neg):
                in_brace = False
            acc += char
            continue
        elif char == "[":
            in_brace = True
            brace_start = i
            brace_neg = False
            acc += char
            continue
        if (
            char in _EXTGLOB_TYPES
            and text[i:i + 1] == "("
            and (ext_depth <= _MAX_EXTGLOB_RECURSION or ast.can_adopt_type(char))
        ):
            depth_add = 0 if ast.can_adopt_type(char) else 1
            part.push(acc)
            acc = ""
            ext = _Ast(char, part)
            part.push(ext)
            i = _parse_ast(text, ext, i, ext_depth + depth_add, nesting + 1)
            continue
        if char == "|":
            part.push(acc)
            acc = ""
            parts.append(part)
            part = _Ast(None, ast)
            continue
        if char == ")":
            if acc == "" and len(ast.parts) == 0:
                ast.empty_ext = True
            part.push(acc)
            acc = ""
            ast.push(*(parts + [part]))
            return i
        acc += char
    # unfinished extglob: the whole remainder is literal text
    ast.type = None
    ast.has_magic = None
    ast.parts = [text[pos - 1:]]
    return i


_UNICODE_BAD_ESCAPES = set("-,#!") | _RE_ESCAPED - set("^$\\.*+?()[]{}|")


def _check_unicode_escapes(source):
    """minimatch compiles posix classes with the u flag, where escaping - , # !
    or whitespace outside a class is a syntax error, so such a pattern throws."""
    in_class = False
    i = 0
    while i < len(source):
        char = source[i]
        if char == "\\":
            if not in_class and source[i + 1:i + 2] in _UNICODE_BAD_ESCAPES:
                raise SelectionError(
                    "unsupported artifact pattern: minimatch rejects a posix class combined with {0!r}".format(
                        source[i + 1]
                    )
                )
            i += 2
            continue
        if char == "[" and not in_class:
            in_class = True
        elif char == "]" and in_class:
            in_class = False
        i += 1


def _translate_part(glob, budget):
    """Return (kind, value) for one pattern part: ('re', source, uflag) or
    ('literal', text)."""
    ast = _Ast(None, None, budget)
    _parse_ast(glob, ast, 0, 0)
    source, uflag, magic = ast.to_re()
    if uflag:
        _check_unicode_escapes(source)
    if not magic and not ast.has_magic:
        return ("literal", _unescape(source), False)
    return ("re", source, uflag)


def _reject_escaped_pipe(text):
    escaping = False
    for char in text:
        if escaping:
            escaping = False
            if char == "|":
                raise SelectionError(
                    "unsupported escaped pipe in artifact pattern: minimatch turns it into regex alternation"
                )
        elif char == "\\":
            escaping = True


def _part_matcher(glob, budget):
    if glob == "**":
        return lambda raw, units: not raw.startswith(".")
    if glob == "":
        return lambda raw, units: raw == ""
    units_glob = _utf16_units(glob)
    if _STAR_ONLY.fullmatch(glob):
        return lambda raw, units: len(units) != 0 and not units.startswith(".")
    found = _STAR_EXT.fullmatch(glob)
    if found:
        _reject_escaped_pipe(found.group(1))
        ext = _utf16_units(found.group(1))
        return lambda raw, units: not units.startswith(".") and units.endswith(ext)
    found = _QMARKS.fullmatch(glob)
    if found:
        _reject_escaped_pipe(found.group(1) or "")
        size = len(units_glob)
        ext = _utf16_units(found.group(1) or "")
        return lambda raw, units: (
            len(units) == size and not units.startswith(".") and (not ext or units.endswith(ext))
        )
    if _STAR_DOT_STAR.fullmatch(glob):
        return lambda raw, units: not units.startswith(".") and "." in units
    if _DOT_STAR.fullmatch(glob):
        return lambda raw, units: units not in (".", "..") and units.startswith(".")
    kind, value, uflag = _translate_part(glob, budget)
    if kind == "literal":
        return lambda raw, units: raw == value
    if not uflag and units_glob != glob:
        kind, value, uflag = _translate_part(units_glob, budget)
        if kind == "literal":
            return lambda raw, units: units == value
    try:
        compiled = re.compile(value)
    except (re.error, OverflowError, RecursionError) as error:
        raise SelectionError(
            "unsupported artifact pattern part {0!r}: {1}".format(glob, error)
        ) from error
    if uflag:
        return lambda raw, units: compiled.fullmatch(raw) is not None
    return lambda raw, units: compiled.fullmatch(units) is not None


@functools.lru_cache(maxsize=128)
def _compile_pattern(pattern):
    if len(pattern) > _MAX_PATTERN_LENGTH:
        raise SelectionError("artifact pattern is too long")
    if pattern[:1] == "#":
        return lambda name: False
    if pattern == "":
        return lambda name: name == ""
    negate = False
    offset = 0
    while offset < len(pattern) and pattern[offset] == "!":
        negate = not negate
        offset += 1
    body = pattern[offset:]
    alternatives = []
    budget = _Budget()
    for item in expand_braces(body):
        if "/" in item:
            # A name never contains "/", so a multi-segment alternative cannot
            # match. Only globstar and ".." segments change that, and those are
            # not reproduced here.
            if any(segment in ("**", "..") for segment in re.split("/+", item)):
                raise SelectionError(
                    "unsupported path segment in artifact pattern: {0}".format(pattern)
                )
            continue
        alternatives.append(_part_matcher(item, budget))

    def match(name):
        if "/" in name:
            return False
        units = _utf16_units(name)
        for alternative in alternatives:
            if alternative(name, units):
                return not negate
        return negate

    return match


def matches(name, pattern):
    """Match an artifact name like minimatch 10 with default options."""
    return _compile_pattern(pattern)(name)


def gh_api(path):
    result = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, check=False, timeout=60
    )
    if result.returncode != 0:
        raise SelectionError("gh api {0} failed: {1}".format(path, result.stderr.strip()))
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise SelectionError("gh api {0} returned invalid JSON".format(path)) from error


def parse_timestamp(value, label):
    if not isinstance(value, str) or not TIMESTAMP.match(value):
        raise SelectionError("{0}: missing or ambiguous timestamp: {1!r}".format(label, value))
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def list_artifacts(repository, run_id, fetch):
    artifacts = []
    page = 1
    while True:
        document = fetch(
            "/repos/{0}/actions/runs/{1}/artifacts?per_page={2}&page={3}".format(
                repository, run_id, PAGE_SIZE, page
            )
        )
        batch = document.get("artifacts") if isinstance(document, dict) else None
        if not isinstance(batch, list):
            raise SelectionError("artifact listing page {0} is malformed".format(page))
        artifacts.extend(batch)
        total = document.get("total_count")
        if len(batch) < PAGE_SIZE or (isinstance(total, int) and len(artifacts) >= total):
            return artifacts
        page += 1


def select_artifacts(repository, run_id, attempt, pattern, fetch=gh_api):
    matches("", pattern)
    attempt_run = fetch(
        "/repos/{0}/actions/runs/{1}/attempts/{2}".format(repository, run_id, attempt)
    )
    started_at = parse_timestamp(
        attempt_run.get("run_started_at") if isinstance(attempt_run, dict) else None,
        "attempt run_started_at",
    )
    selected = []
    for artifact in list_artifacts(repository, run_id, fetch):
        name = artifact.get("name")
        if not isinstance(name, str) or not matches(name, pattern):
            continue
        if artifact.get("expired") is True:
            continue
        created_at = parse_timestamp(
            artifact.get("created_at"), "artifact {0} created_at".format(name)
        )
        if created_at > started_at:
            artifact_id = artifact.get("id")
            if not isinstance(artifact_id, int) or isinstance(artifact_id, bool):
                raise SelectionError("artifact {0} has no integer id".format(name))
            selected.append(artifact_id)
    return sorted(set(selected))


def write_output(path, ids):
    if path is not None:
        with open(path, "a") as handle:
            handle.write("artifact_ids={0}\n".format(",".join(str(i) for i in ids)))


def main(argv=None, fetch=gh_api):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args(argv)
    try:
        ids = select_artifacts(
            args.repository, args.run_id, args.run_attempt, args.pattern, fetch
        )
    except SelectionError as error:
        write_output(args.github_output, [])
        print("error: {0}".format(error), file=sys.stderr)
        return 1
    write_output(args.github_output, ids)
    print("selected {0} artifact(s) from attempt {1}".format(len(ids), args.run_attempt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
