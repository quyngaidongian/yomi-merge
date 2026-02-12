import re

RUS_HEADER_RE = re.compile(
    r"""
    ^\s*
    @.*\n                 # Dòng 1: @term
    °[^\n]*\n             # Dòng 2: ° phonetic + POS + index
    """,
    re.VERBOSE,
)


def normalize_definitions(
    raw_definitions,
    term,
    lang="auto",
    source="dict2",
):
    """
    Normalize raw definitions from Dict2 (Russian-style dictionaries).

    Behavior:
    - Remove duplicated headword lines (term + phonetic/POS header)
    - Preserve meaning blocks, notes, examples
    - Return list[str], usually with 1 cleaned definition block
    """

    if not raw_definitions:
        return []

    normalized = []

    for item in raw_definitions:
        if not isinstance(item, str):
            text = str(item)
        else:
            text = item

        text = text.strip()
        if not text:
            continue

        # Normalize newlines
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Remove duplicated term at very top (exact match)
        lines = text.split("\n")
        if lines and lines[0].strip() == term:
            text = "\n".join(lines[1:]).lstrip()

        # Remove Russian dictionary header (phonetics + POS)
        text = RUS_HEADER_RE.sub("", text).lstrip()

        # Cleanup excessive blank lines (but keep structure)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        if text:
            normalized.append(text)

    return normalized
