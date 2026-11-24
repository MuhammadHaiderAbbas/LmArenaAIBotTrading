"""Text extraction, sanitisation and chunking.

The two jobs here are dull and load-bearing:

1. **Sanitisation.** Retrieved content is untrusted. It arrives with HTML, hidden
   text, invisible Unicode, and — in the general case — sentences engineered to
   look like instructions to whatever reads them next ("ignore previous
   instructions and…"). Every document is scanned and the finding is recorded on
   the row, so a reviewer can see detection happened rather than trust that it
   didn't.

2. **Chunking that preserves location.** A chunk that cannot be traced back to a
   page or section cannot be cited, and an uncitable sentence is a rumour. Chunks
   carry `page`, `section` and character offsets.

Nothing here interprets content. Interpretation happens later, and is labelled.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
HEADING_RE = re.compile(r"^(?:[A-Z][A-Za-z0-9 ,&/\-]{2,80})$")

# Patterns that indicate an attempt to redirect a downstream reader. These are
# signals, not proof: a legitimate article about prompt injection will trip them
# too, which is why a finding quarantines nothing on its own and is always
# surfaced rather than silently acted on.
INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget)\b[^.]{0,40}\b(previous|prior|above|earlier)\b[^.]{0,30}"
            r"\b(instruction|prompt|rule|direction)",
            re.I,
        ),
    ),
    (
        "role_hijack",
        re.compile(
            r"\byou are now\b|\bact as (?:an?|the)\b[^.]{0,40}\b(admin|system|developer)", re.I
        ),
    ),
    (
        "system_prompt_probe",
        re.compile(r"\b(system prompt|initial prompt|developer message)\b", re.I),
    ),
    (
        "tool_invocation",
        re.compile(r"\b(execute|run|eval)\s+(?:the\s+)?(?:command|code|script|tool)\b", re.I),
    ),
    (
        "exfiltration",
        re.compile(
            r"\b(send|post|upload|exfiltrate)\b[^.]{0,40}\b(api[_ ]?key|token|credential|password)",
            re.I,
        ),
    ),
    (
        "credential_request",
        re.compile(
            r"\b(provide|enter|paste|supply)\b[^.]{0,30}\b(api[_ ]?key|password|secret)\b", re.I
        ),
    ),
)

ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.I | re.S)
HTML_TAG_RE = re.compile(r"<[^>]+>")
MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


@dataclass(slots=True)
class Sanitised:
    text: str
    flags: list[str]
    removed_hidden: int


@dataclass(slots=True)
class Chunk:
    ordinal: int
    text: str
    page: int | None
    section: str
    char_start: int
    char_end: int
    token_estimate: int


def sanitise(raw: str) -> Sanitised:
    """Reduce arbitrary text/HTML to plain text and record hostile patterns.

    HTML tags are stripped rather than parsed: the pipeline never needs the DOM,
    and not building one removes a whole class of parser-differential attacks.
    """
    if not raw:
        return Sanitised("", [], 0)

    text = SCRIPT_STYLE_RE.sub(" ", raw)
    text = HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)

    removed_hidden = 0
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        # "Hidden text" in the wild means zero-width characters and control
        # codes used to smuggle instructions past a human reviewer.
        without_zero = ZERO_WIDTH_RE.sub("", line)
        removed_hidden += len(line) - len(without_zero)
        without_control = CONTROL_RE.sub("", without_zero)
        removed_hidden += len(without_zero) - len(without_control)
        cleaned_lines.append(without_control)

    text = "\n".join(cleaned_lines)
    text = unicodedata.normalize("NFKC", text)
    text = MULTI_SPACE_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    flags = detect_injection(text)
    return Sanitised(text=text, flags=flags, removed_hidden=removed_hidden)


def detect_injection(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(text) and name not in found:
            found.append(name)
    return found


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def simhash(text: str, bits: int = 32) -> str:
    """Near-duplicate fingerprint.

    Wire services republish the same story under different URLs, so exact-hash
    deduplication alone lets duplicates through. Hamming distance on a 32-bit
    simhash catches those.
    """
    if not text:
        return "0" * (bits // 4)
    tokens = [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2]
    if not tokens:
        return "0" * (bits // 4)
    weights = [0] * bits
    for token in tokens:
        digest = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
        for index in range(bits):
            weights[index] += 1 if (digest >> index) & 1 else -1
    value = 0
    for index in range(bits):
        if weights[index] > 0:
            value |= 1 << index
    return f"{value:0{bits // 4}x}"


def hamming(left: str, right: str) -> int:
    if len(left) != len(right):
        return 64
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except ValueError:
        return 64


def chunk_text(
    text: str,
    *,
    max_chars: int = 900,
    overlap: int = 120,
) -> list[Chunk]:
    """Split into overlapping chunks, preserving section and page locators.

    Chunks break on paragraph then sentence boundaries, never mid-sentence, so a
    quote lifted from a chunk is always a complete thought a reader can check.
    """
    if not text.strip():
        return []

    chunks: list[Chunk] = []
    section = ""
    ordinal = 0
    cursor = 0

    paragraphs = [part.strip() for part in PARAGRAPH_SPLIT.split(text) if part.strip()]
    buffer = ""
    buffer_start = 0

    def flush(end: int) -> None:
        nonlocal buffer, buffer_start, ordinal
        if not buffer.strip():
            return
        body = buffer.strip()
        chunks.append(
            Chunk(
                ordinal=ordinal,
                text=body,
                page=None,
                section=section,
                char_start=buffer_start,
                char_end=end,
                token_estimate=max(len(body) // 4, 1),
            )
        )
        ordinal += 1
        tail = ""
        if overlap and len(body) > overlap:
            # Cut on a word boundary: a chunk that begins mid-word produces
            # citations like "erm inflation outlook" that look corrupted.
            candidate = body[-overlap:]
            space = candidate.find(" ")
            tail = candidate[space + 1 :] if space > 0 else ""
        buffer = tail
        buffer_start = max(end - len(tail), 0)

    for paragraph in paragraphs:
        # A short all-caps or title-case line standing alone is treated as a
        # heading: it labels every chunk that follows until the next one.
        if len(paragraph) < 80 and HEADING_RE.match(paragraph) and not paragraph.endswith("."):
            if buffer.strip():
                flush(cursor)
            section = paragraph
            buffer = ""
            buffer_start = cursor
            cursor += len(paragraph) + 1
            continue

        if len(paragraph) > max_chars:
            for sentence in SENTENCE_SPLIT.split(paragraph):
                if len(buffer) + len(sentence) + 1 > max_chars and buffer.strip():
                    flush(cursor)
                if not buffer:
                    buffer_start = cursor
                buffer += sentence + " "
                cursor += len(sentence) + 1
            continue

        if len(buffer) + len(paragraph) + 2 > max_chars and buffer.strip():
            flush(cursor)
            buffer_start = cursor
        if not buffer:
            buffer_start = cursor
        buffer += paragraph + "\n\n"
        cursor += len(paragraph) + 2

    if buffer.strip():
        chunks.append(
            Chunk(
                ordinal=ordinal,
                text=buffer.strip(),
                page=None,
                section=section,
                char_start=buffer_start,
                char_end=cursor,
                token_estimate=max(len(buffer.strip()) // 4, 1),
            )
        )

    return chunks


def page_offsets(pages: list[str], *, max_chars: int = 900, overlap: int = 120) -> list[Chunk]:
    """Chunk a multi-page document (a PDF), keeping the page number on each chunk."""
    chunks: list[Chunk] = []
    ordinal = 0
    for page_number, page_text in enumerate(pages, start=1):
        for chunk in chunk_text(page_text, max_chars=max_chars, overlap=overlap):
            chunk.ordinal = ordinal
            chunk.page = page_number
            ordinal += 1
            chunks.append(chunk)
    return chunks


def key_phrases(text: str, limit: int = 6) -> list[str]:
    """Cheap noun-ish phrase extraction for entity tagging.

    Deliberately conservative: capitalised multi-word sequences and known
    currency codes. A wrong entity link is worse than a missing one, because a
    report will cite it.
    """
    from app.services.research.vector import STOPWORDS, tokenise

    candidates: list[str] = []
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,3})\b", text):
        phrase = match.group(1).strip(" .-")
        if len(phrase) < 3:
            continue
        if all(word.lower() in STOPWORDS for word in phrase.split()):
            continue
        if phrase not in candidates:
            candidates.append(phrase)
        if len(candidates) >= limit * 3:
            break

    if not candidates:
        tokens = tokenise(text)
        seen: list[str] = []
        for token in tokens:
            if token not in seen:
                seen.append(token)
            if len(seen) >= limit:
                break
        return seen

    return candidates[:limit]
