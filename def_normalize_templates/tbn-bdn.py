import re

BRACE_BLOCK_RE = re.compile(r"(\s*\{[^}]+\})")

POS_PREFIX_RE = re.compile(
    r"^\s*(n|v|adj|adv|prep|conj|pron|interj)\.\s*",
    re.IGNORECASE
)

def normalize_definitions(
    raw_definitions,
    term,
    lang="auto",
    source="dict2",
):
    """
    Normalize raw definitions from Dict2.

    Applied rules:
    - Ensure list[str]
    - Strip leading term repetition
    - Replace ', (' with '\\n('
    - Split multiple {...} blocks onto separate lines
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

        # Remove leading term repetition (very conservative)
        if text.lower().startswith(term.lower()):
            text = text[len(term):].lstrip(" .:-")

        # Rule 1: ", (" → newline
        text = text.replace(", (", "\n(")
        text = text.replace(" {", "\n{")

        # Rule 2: split multiple {...} blocks onto new lines
        parts = BRACE_BLOCK_RE.split(text)

        rebuilt_lines = []
        current = ""

        for part in parts:
            if not part:
                continue

            if part.startswith("{"):
                if current:
                    rebuilt_lines.append(current.strip())
                current = part.strip()
            else:
                if current:
                    current += part
                else:
                    current = part.strip()

        if current:
            rebuilt_lines.append(current.strip())

        normalized.extend(rebuilt_lines)

    return normalized