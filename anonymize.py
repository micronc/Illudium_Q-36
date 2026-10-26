"""
Redact IP addresses and common PII from uploaded intel text.

Replacements use sequential placeholders such as <<IP_001>>, <<EMAIL_001>>.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# IPv4 (avoid matching versions like 1.2.3 alone by requiring 4 octets)
_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
)

# Simplified IPv6 (common forms; prefer compressed+trailing hextets)
_IPV6 = re.compile(
    r"(?<![0-9A-Fa-f:])(?:"
    r"(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}"
    r"|(?:[A-Fa-f0-9]{1,4}:){1,6}:[A-Fa-f0-9]{1,4}"
    r"|(?:[A-Fa-f0-9]{1,4}:){1,5}(?::[A-Fa-f0-9]{1,4}){1,2}"
    r"|(?:[A-Fa-f0-9]{1,4}:){1,4}(?::[A-Fa-f0-9]{1,4}){1,3}"
    r"|(?:[A-Fa-f0-9]{1,4}:){1,3}(?::[A-Fa-f0-9]{1,4}){1,4}"
    r"|(?:[A-Fa-f0-9]{1,4}:){1,2}(?::[A-Fa-f0-9]{1,4}){1,5}"
    r"|[A-Fa-f0-9]{1,4}:(?::[A-Fa-f0-9]{1,4}){1,6}"
    r"|:(?::[A-Fa-f0-9]{1,4}){1,7}"
    r"|(?:[A-Fa-f0-9]{1,4}:){1,7}:"
    r")(?![0-9A-Fa-f:])"
)

_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# NANP-ish phones
_PHONE = re.compile(
    r"(?<!\w)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\w)"
)

_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Absolute Windows / Unix home-ish paths (redact directory prefix, keep basename)
_WIN_PATH = re.compile(
    r"(?i)\b[A-Z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)+"
)
_UNIX_HOME = re.compile(
    r"(?m)(?:^|[\s\"'=])(/home/[^/\s\"']+/|/Users/[^/\s\"']+/|/mnt/c/Users/[^/\s\"']+/)"
)

_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".log",
    ".xml",
    ".yaml",
    ".yml",
    ".ioc",
    ".stix",
}


@dataclass
class Anonymizer:
    ip_n: int = 0
    email_n: int = 0
    phone_n: int = 0
    ssn_n: int = 0
    path_n: int = 0
    map: dict[str, str] = field(default_factory=dict)
    hits: list[dict[str, str]] = field(default_factory=list)

    def _token(self, kind: str, counter_attr: str) -> str:
        n = getattr(self, counter_attr) + 1
        setattr(self, counter_attr, n)
        return f"<<{kind}_{n:03d}>>"

    def _sub(self, kind: str, counter_attr: str, match: re.Match) -> str:
        raw = match.group(0)
        # Preserve leading whitespace/quote for unix-home pattern group edge
        if kind == "PATH" and raw and raw[0] in " \t\"'=":
            lead, body = raw[0], raw[1:]
        else:
            lead, body = "", raw
        key = body
        if key not in self.map:
            token = self._token(kind, counter_attr)
            self.map[key] = token
            self.hits.append({"kind": kind, "original": key, "placeholder": token})
        return lead + self.map[key]

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        out = text
        out = _EMAIL.sub(lambda m: self._sub("EMAIL", "email_n", m), out)
        out = _SSN.sub(lambda m: self._sub("SSN", "ssn_n", m), out)
        out = _PHONE.sub(lambda m: self._sub("PHONE", "phone_n", m), out)
        out = _IPV4.sub(lambda m: self._sub("IP", "ip_n", m), out)
        out = _IPV6.sub(lambda m: self._sub("IP", "ip_n", m), out)
        out = _WIN_PATH.sub(lambda m: self._sub("PATH", "path_n", m), out)
        out = _UNIX_HOME.sub(lambda m: self._sub("PATH", "path_n", m), out)
        return out

    def summary(self) -> dict[str, Any]:
        counts = {
            "ip": self.ip_n,
            "email": self.email_n,
            "phone": self.phone_n,
            "ssn": self.ssn_n,
            "path": self.path_n,
            "total": len(self.hits),
        }
        return {
            "counts": counts,
            "replacements": self.hits[:200],  # cap for UI
            "truncated": len(self.hits) > 200,
        }


def is_text_intel(filename: str) -> bool:
    lower = (filename or "").lower()
    return any(lower.endswith(sfx) for sfx in _TEXT_SUFFIXES)


def anonymize_bytes(filename: str, data: bytes) -> tuple[bytes, Anonymizer | None]:
    """Return possibly redacted bytes and the anonymizer used (or None if skipped)."""
    if not is_text_intel(filename):
        return data, None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    anon = Anonymizer()
    redacted = anon.redact_text(text)
    if redacted == text and anon.summary()["counts"]["total"] == 0:
        return data, anon
    return redacted.encode("utf-8"), anon


def warning_message(file_count: int) -> str:
    n = max(0, int(file_count))
    return (
        f'You have just uploaded "{n}" file(s). To make sure IP addresses or PII '
        "is uploaded, I will now replace those with sequential numbers as "
        "place holders."
    )
