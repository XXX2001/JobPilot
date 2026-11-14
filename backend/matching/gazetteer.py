# backend/matching/gazetteer.py
"""Curated bilingual skill gazetteer: exact/alias lookup over accent-folded tokens.

Text is tokenised on word boundaries and looked up n-gram by n-gram, longest alias
first, so a skill never matches inside a longer word ("Go" in "going", "C" in "CDI").
No embeddings, no LLM, no network.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

SKILLS_PATH = Path(__file__).resolve().parent / "data" / "skills.txt"


def file_version(path: Path) -> str:
    """First 12 hex chars of the file's sha256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


GAZETTEER_VERSION: str = file_version(SKILLS_PATH)


class Mention(NamedTuple):
    skill: str  # canonical name
    start: int  # character span in the original text
    end: int


def fold(text: str) -> str:
    """NFKD, strip accents, lowercase."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _strip_accents(text: str) -> tuple[str, list[int] | None]:
    """Accent-stripped text (case kept) and, per output char, its index in ``text``.

    The index map is ``None`` when the text is ASCII (output == input).
    """
    if text.isascii():
        return text, None
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch < "\x80":
            out.append(ch)
            index.append(i)
            continue
        for d in unicodedata.normalize("NFKD", ch):
            if not unicodedata.combining(d):
                out.append(d)
                index.append(i)
    return "".join(out), index


# French elisions ("C'est", "d'Airflow") and "R&D" would otherwise leave a lone C, D or R.
_NOISE = re.compile(
    r"\b(?:c|d|l|j|n|m|s|t|qu|jusqu|lorsqu|puisqu)['’]|\bR\s?&\s?D\b", re.IGNORECASE
)
# A token is a run of letters/digits, with inner dots (node.js), a leading dot (.net)
# and trailing + or # (c++, c#). Everything else separates ("ci/cd" -> ci, cd).
_TOKEN = re.compile(r"(?<![^\W_])\.?[^\W_]+(?:\.[^\W_]+)*[+#]*")


def _blank(match: re.Match[str]) -> str:
    return " " * len(match.group())


def _tokens(stripped: str) -> list[tuple[str, int, int]]:
    """(token, start, end) over accent-stripped text, original case."""
    return [(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(_NOISE.sub(_blank, stripped))]


def _key(alias: str) -> tuple[str, ...]:
    return tuple(tok for tok, _, _ in _tokens(_strip_accents(alias)[0]))


class Gazetteer:
    def __init__(
        self, entries: Iterable[tuple[str, str, bool]], families: dict[str, str] | None = None
    ) -> None:
        """entries: (canonical, alias, case_sensitive). The first claim on an alias wins."""
        self._ci: dict[tuple[str, ...], str] = {}
        self._cs: dict[tuple[str, ...], str] = {}
        self._families = families or {}
        for canon, alias, case_sensitive in entries:
            key = _key(alias)
            if not key:
                continue
            if case_sensitive:
                self._cs.setdefault(key, canon)
            else:
                self._ci.setdefault(tuple(t.lower() for t in key), canon)
        self._max_len = max((len(k) for k in (*self._ci, *self._cs)), default=0)

    @classmethod
    def load(cls, extra_terms: Iterable[str] = (), *, path: Path = SKILLS_PATH) -> Gazetteer:
        """The curated list, plus ``extra_terms`` (the owner's search keywords).

        An extra term that is not already an alias becomes a skill of its own, so a
        profession the curated list does not cover still counts.
        """
        entries: list[tuple[str, str, bool]] = []
        families: dict[str, str] = {}
        family: str | None = None
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("@"):
                family = line[1:].strip()
                continue
            parts = [p.strip() for p in line.split("|")]
            canon = parts[0].lstrip("!").lower()
            if family:
                families[canon] = family
            entries += [(canon, p.lstrip("!"), p.startswith("!")) for p in parts if p]
        gazetteer = cls(entries, families)
        case_sensitive_lowered = {tuple(t.lower() for t in k) for k in gazetteer._cs}
        for term in extra_terms:
            key = tuple(t.lower() for t in _key(term))
            # A keyword spelled like a case-sensitive alias ("go") must not loosen it.
            if key and key not in case_sensitive_lowered:
                gazetteer._ci.setdefault(key, " ".join(term.lower().split()))
                gazetteer._max_len = max(gazetteer._max_len, len(key))
        return gazetteer

    def extract(self, text: str) -> list[Mention]:
        """Every skill mention in reading order, longest alias first, non-overlapping."""
        stripped, index = _strip_accents(text)
        toks = _tokens(stripped)
        exact = [t for t, _, _ in toks]
        lowered = [t.lower() for t in exact]
        mentions: list[Mention] = []
        i = 0
        while i < len(toks):
            for n in range(min(self._max_len, len(toks) - i), 0, -1):
                canon = self._cs.get(tuple(exact[i : i + n])) or self._ci.get(
                    tuple(lowered[i : i + n])
                )
                if canon:
                    start, last = toks[i][1], toks[i + n - 1][2] - 1
                    if index is not None:
                        start, last = index[start], index[last]
                    mentions.append(Mention(canon, start, last + 1))
                    i += n
                    break
            else:
                i += 1
        return mentions

    def skills(self, text: str) -> set[str]:
        return {m.skill for m in self.extract(text)}

    def related(self, a: str, b: str) -> bool:
        """Both skills are in the same ``@family`` (families ending in "!" give no credit)."""
        family = self._families.get(a)
        return family is not None and family == self._families.get(b) and not family.endswith("!")
