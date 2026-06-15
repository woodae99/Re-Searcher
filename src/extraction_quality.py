"""Computable extraction-quality profile for the v0.6 extraction router.

The W1 bake-off ranked extractors by `searchable_chars` plus a handful of binary
artifact flags, then a human eyeballed the Markdown to pick a winner. That does
not scale to a corpus and, worse, the headline metric (char count) ranked the
*losing* extractor first on the Hudson stress test. This module replaces the
eyeball with a transparent, computable score so routing decisions are measured.

`profile_text` turns extracted text into a `QualityProfile`:

- a set of normalized **signal** values (each a property of the text),
- a set of **penalties** in [0, 1] derived from those signals (0 = no problem),
- an **overall_score** in [0, 1] (1 = clean) and a **grade**,
- a routing **action**: ``accept`` / ``clean`` / ``escalate`` / ``unusable``.

All thresholds live in `QualityThresholds` so the eventual config-driven router
tunes one object, not scattered constants. The dictionary-based real-word ratio
is the key new signal: OCR scramble like ``ECUTI SIONALS PROFES VES`` produces
tokens that are in no dictionary, which char count and the old flags missed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# ── Token / line patterns ────────────────────────────────────────────────────
_ALPHA_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)  # unicode letters, no digits
_LETTER_SPACED_RE = re.compile(r"(?:\b[^\W\d_]\s+){6,}[^\W\d_]\b", re.UNICODE)
_HYPHEN_BREAK_RE = re.compile(r"\w-\s*\n\s*\w")
_INTERWORD_GAP_RE = re.compile(r"\S( +)\S")
_MD_IMAGE_DATA_RE = re.compile(r"^\s*!\[Image\]\(data:image/", re.IGNORECASE)
_MD_IMAGE_PLACEHOLDER_RE = re.compile(r"==>\s*picture.*intentionally omitted\s*<==", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_LIGATURE_MAP = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}

# Default dictionary wordlists (Debian/Ubuntu `wordlists` package). English plus
# the European languages most likely in a coaching-theory corpus, so legitimate
# non-English sources are not scored as garbage.
DEFAULT_WORDLISTS: Sequence[str] = (
    "/usr/share/dict/american-english",
    "/usr/share/dict/british-english",
    "/usr/share/dict/french",
    "/usr/share/dict/ngerman",
    "/usr/share/dict/spanish",
    "/usr/share/dict/italian",
    "/usr/share/dict/portuguese",
)

_DICTIONARY_CACHE: Dict[tuple, frozenset] = {}


def load_dictionary(paths: Sequence[str] = DEFAULT_WORDLISTS) -> frozenset:
    """Load and cache a lowercased set of dictionary words (length >= 2)."""
    key = tuple(paths)
    cached = _DICTIONARY_CACHE.get(key)
    if cached is not None:
        return cached
    words: set = set()
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            word = line.strip().lower()
            # Wordlists include possessives ("cat's"); keep the base token only.
            word = word.split("'", 1)[0]
            if len(word) >= 2 and word.isalpha():
                words.add(word)
    frozen = frozenset(words)
    _DICTIONARY_CACHE[key] = frozen
    return frozen


@dataclass(frozen=True)
class QualityThresholds:
    """Tunable bands. Penalties ramp linearly between the good and bad anchors.

    For each signal, a value at or beyond ``*_good`` scores 0 penalty and a value
    at or beyond ``*_bad`` scores 1.0; values in between interpolate.
    """

    # Real-word ratio (higher is better, so good > bad).
    real_word_good: float = 0.70
    real_word_bad: float = 0.35
    # Fragmentation: fraction of alpha tokens of length <= 2 (lower is better).
    # ~0.21 is normal for academic English prose (of/a/to/in/is...), so the
    # "good" anchor sits above that and only real fragmentation is penalized.
    short_token_good: float = 0.24
    short_token_bad: float = 0.45
    # Spacing noise: fraction of inter-word gaps that are 2+ spaces.
    double_space_good: float = 0.05
    double_space_bad: float = 0.30
    # Line-break hyphenation breaks per 1000 tokens.
    hyphen_rate_good: float = 5.0
    hyphen_rate_bad: float = 40.0
    # Single-token lines / nonempty lines (layout fragmentation).
    single_word_line_good: float = 0.15
    single_word_line_bad: float = 0.55
    # Repeated nonempty lines / nonempty lines (header/footer boilerplate).
    repeated_line_good: float = 0.08
    repeated_line_bad: float = 0.35
    # Replacement/control chars per 1000 chars.
    replacement_good: float = 0.2
    replacement_bad: float = 4.0
    # Letter-spaced runs per 1000 tokens.
    letter_spaced_good: float = 0.0
    letter_spaced_bad: float = 3.0
    # Minimum usable text. Below min_alpha_tokens => emptiness dominates.
    min_alpha_tokens: int = 40
    sparse_chars_per_page: float = 200.0  # only used when page count is known
    # Below this Latin-token fraction the dictionary real-word check is
    # unreliable (non-Latin script dominant), so the garbage penalty is skipped.
    min_latin_ratio_for_dict: float = 0.50

    # Penalty weights (need not sum to 1; normalized internally).
    weights: Dict[str, float] = field(
        default_factory=lambda: {
            "garbage": 0.30,
            "fragmentation": 0.20,
            "spacing": 0.12,
            "line_damage": 0.10,
            "single_word_lines": 0.08,
            "boilerplate": 0.08,
            "encoding": 0.06,
            "letter_spaced": 0.06,
        }
    )

    # Which penalties signal genuine extractor failure (a *different* extractor
    # might fix) vs deterministically recoverable noise (whitespace/hyphen/dedup
    # cleanup fixes it in place, with no need to escalate to a slower extractor).
    fundamental_penalties: Sequence[str] = ("garbage", "fragmentation", "encoding", "letter_spaced")
    recoverable_penalties: Sequence[str] = ("spacing", "line_damage", "boilerplate", "single_word_lines")

    # Routing is bucket-driven, not driven by the blended overall_score:
    #   fundamental high      -> escalate (heavier extractor)
    #   else recoverable high -> clean (deterministic cleanup, re-score)
    #   else                  -> accept
    fundamental_escalate_at: float = 0.35
    recoverable_clean_at: float = 0.25

    # Grade bands on overall_score (1.0 best), for ranking/reporting only.
    accept_at: float = 0.80
    clean_at: float = 0.62
    escalate_at: float = 0.40


@dataclass(frozen=True)
class QualityProfile:
    grade: str
    action: str
    overall_score: float
    fundamental_penalty: float
    recoverable_penalty: float
    signals: Dict[str, float]
    penalties: Dict[str, float]
    notes: List[str]

    def to_dict(self) -> Dict:
        return asdict(self)


def _is_latin(token: str) -> bool:
    """True if the token's letters are all Latin script (incl. accented forms).

    Cheap codepoint test: Latin blocks live below U+0250 (Basic Latin, Latin-1
    Supplement, Latin Extended-A/B). Cyrillic/Greek/CJK/Arabic fall above it.
    """
    return all(ord(ch) < 0x250 for ch in token)


def _ramp(value: float, good: float, bad: float) -> float:
    """Linear penalty in [0,1]; supports both directions (good<bad or good>bad)."""
    if good == bad:
        return 0.0 if value == good else 1.0
    frac = (value - good) / (bad - good)
    return max(0.0, min(1.0, frac))


def strip_non_text(text: str) -> str:
    """Drop Markdown image payload lines and 'image omitted' placeholders."""
    kept = []
    for line in (text or "").splitlines():
        if _MD_IMAGE_DATA_RE.search(line):
            continue
        if _MD_IMAGE_PLACEHOLDER_RE.search(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def deterministic_clean(text: str) -> str:
    """Cheap, lossless-for-retrieval cleanup that fixes 'recoverable' noise.

    Targets exactly the penalties in the recoverable bucket (spacing, line-break
    hyphenation) plus ligature/NFKC normalization. Deliberately conservative: it
    does not drop repeated lines (boilerplate dedup is a separate, riskier pass)
    and preserves paragraph structure. This is what a ``clean`` action runs
    before re-scoring, instead of escalating to a heavier extractor.
    """
    t = text or ""
    # De-hyphenate words split across a line break: "exam-\nple" -> "example".
    t = re.sub(r"(\w)-[ \t]*\n[ \t]*(\w)", r"\1\2", t)
    for ligature, replacement in _LIGATURE_MAP.items():
        t = t.replace(ligature, replacement)
    t = unicodedata.normalize("NFKC", t)
    # Collapse intra-line runs of spaces/tabs; keep newlines.
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def compute_signals(text: str, *, dictionary: frozenset, pages: Optional[int] = None) -> Dict[str, float]:
    text = strip_non_text(text or "")
    normalized = unicodedata.normalize("NFKC", text)

    tokens = _ALPHA_TOKEN_RE.findall(normalized)
    alpha_tokens = len(tokens)

    # Real-word ratio is only meaningful for scripts we have a wordlist for, so
    # restrict it to Latin-script tokens and report how Latin-dominant the text
    # is. Cyrillic/Greek/CJK/etc. clean text would otherwise read as "garbage".
    latin_tokens = [t for t in tokens if _is_latin(t)]
    real_words = sum(1 for t in latin_tokens if t.lower() in dictionary)
    short_tokens = sum(1 for t in tokens if len(t) <= 2)
    single_char_tokens = sum(1 for t in tokens if len(t) == 1)

    lines = [ln.strip() for ln in normalized.splitlines()]
    nonempty = [ln for ln in lines if ln]
    nonempty_count = len(nonempty)
    single_word_lines = sum(1 for ln in nonempty if len(ln.split()) == 1)
    repeated_lines = nonempty_count - len(set(nonempty))

    gaps = _INTERWORD_GAP_RE.findall(normalized)
    multi_space_gaps = sum(1 for g in gaps if len(g) >= 2)

    hyphen_breaks = len(_HYPHEN_BREAK_RE.findall(normalized))
    letter_spaced = len(_LETTER_SPACED_RE.findall(normalized))
    replacement_chars = normalized.count("�") + len(_CONTROL_RE.findall(normalized))

    chars = len(normalized)
    per_k_tokens = max(alpha_tokens, 1) / 1000.0
    per_k_chars = max(chars, 1) / 1000.0

    return {
        "chars": float(chars),
        "alpha_tokens": float(alpha_tokens),
        "nonempty_lines": float(nonempty_count),
        "real_word_ratio": (real_words / len(latin_tokens)) if latin_tokens else 0.0,
        "latin_token_ratio": (len(latin_tokens) / alpha_tokens) if alpha_tokens else 0.0,
        "short_token_ratio": (short_tokens / alpha_tokens) if alpha_tokens else 1.0,
        "single_char_token_ratio": (single_char_tokens / alpha_tokens) if alpha_tokens else 0.0,
        "double_space_ratio": (multi_space_gaps / len(gaps)) if gaps else 0.0,
        "hyphen_break_rate": hyphen_breaks / per_k_tokens,
        "single_word_line_ratio": (single_word_lines / nonempty_count) if nonempty_count else 0.0,
        "repeated_line_ratio": (repeated_lines / nonempty_count) if nonempty_count else 0.0,
        "replacement_char_rate": replacement_chars / per_k_chars,
        "letter_spaced_rate": letter_spaced / per_k_tokens,
        "chars_per_page": (chars / pages) if pages else float("nan"),
    }


def profile_text(
    text: str,
    *,
    dictionary: Optional[frozenset] = None,
    thresholds: Optional[QualityThresholds] = None,
    pages: Optional[int] = None,
) -> QualityProfile:
    th = thresholds or QualityThresholds()
    dic = dictionary if dictionary is not None else load_dictionary()
    sig = compute_signals(text, dictionary=dic, pages=pages)
    notes: List[str] = []

    # Emptiness short-circuit: too little text to judge anything else.
    if sig["alpha_tokens"] < th.min_alpha_tokens:
        notes.append(f"near-empty: only {int(sig['alpha_tokens'])} alpha tokens")
        return QualityProfile(
            grade="empty",
            action="escalate",
            overall_score=0.0,
            fundamental_penalty=1.0,
            recoverable_penalty=0.0,
            signals=sig,
            penalties={"emptiness": 1.0},
            notes=notes,
        )

    penalties = {
        "garbage": (
            0.0
            if sig["latin_token_ratio"] < th.min_latin_ratio_for_dict
            else _ramp(sig["real_word_ratio"], th.real_word_good, th.real_word_bad)
        ),
        "fragmentation": _ramp(sig["short_token_ratio"], th.short_token_good, th.short_token_bad),
        "spacing": _ramp(sig["double_space_ratio"], th.double_space_good, th.double_space_bad),
        "line_damage": _ramp(sig["hyphen_break_rate"], th.hyphen_rate_good, th.hyphen_rate_bad),
        "single_word_lines": _ramp(sig["single_word_line_ratio"], th.single_word_line_good, th.single_word_line_bad),
        "boilerplate": _ramp(sig["repeated_line_ratio"], th.repeated_line_good, th.repeated_line_bad),
        "encoding": _ramp(sig["replacement_char_rate"], th.replacement_good, th.replacement_bad),
        "letter_spaced": _ramp(sig["letter_spaced_rate"], th.letter_spaced_good, th.letter_spaced_bad),
    }

    if sig["latin_token_ratio"] < th.min_latin_ratio_for_dict:
        notes.append(
            f"non-Latin script dominant (latin={sig['latin_token_ratio']:.2f}); "
            "dictionary check skipped"
        )

    weight_total = sum(th.weights.values()) or 1.0
    weighted = sum(penalties[k] * th.weights.get(k, 0.0) for k in penalties) / weight_total
    overall = max(0.0, min(1.0, 1.0 - weighted))

    # Bucket scores: weighted penalty within each bucket (normalized by the
    # bucket's own weights so a single-signal bucket isn't diluted).
    def _bucket_score(keys: Sequence[str]) -> float:
        wsum = sum(th.weights.get(k, 0.0) for k in keys) or 1.0
        return sum(penalties.get(k, 0.0) * th.weights.get(k, 0.0) for k in keys) / wsum

    fundamental = _bucket_score(th.fundamental_penalties)
    recoverable = _bucket_score(th.recoverable_penalties)

    # Grade on the blended score (reporting/ranking only).
    if overall >= th.accept_at:
        grade = "good"
    elif overall >= th.clean_at:
        grade = "borderline"
    elif overall >= th.escalate_at:
        grade = "poor"
    else:
        grade = "bad"

    # Action is bucket-driven: only fundamental damage justifies escalation.
    if fundamental >= th.fundamental_escalate_at:
        action = "escalate"
    elif recoverable >= th.recoverable_clean_at:
        action = "clean"
    else:
        action = "accept"

    # Human-readable notes for the worst offenders.
    for key, value in sorted(penalties.items(), key=lambda kv: kv[1], reverse=True):
        if value >= 0.5:
            notes.append(f"{key} penalty {value:.2f}")

    return QualityProfile(
        grade=grade,
        action=action,
        overall_score=round(overall, 4),
        fundamental_penalty=round(fundamental, 4),
        recoverable_penalty=round(recoverable, 4),
        signals=sig,
        penalties={k: round(v, 4) for k, v in penalties.items()},
        notes=notes,
    )
