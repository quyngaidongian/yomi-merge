import re

JP_MARKER_RE = re.compile(r"`\d+`")

JP_POS_RE = re.compile(
    r"^(?:"
    r"n|pn|adv|exp|aux|"
    r"adj(?:-[a-z]+)?|"
    r"v(?:1|5[a-z]?|s|i|t)"
    r")$",
    re.IGNORECASE,
)


def normalize_definitions(
    raw_definitions,
    term,
    lang="auto",
    source="dict2",
):
    """
    Normalize Dict2 definitions (JP-focused).

    Rules:
    - Remove technical markers like `1`, `4`
    - Remove repeated term line
    - Detect POS lines and format as: 〘POS〙
    - Preserve original structure and examples
    """

    if not raw_definitions:
        return []

    normalized = []

    for item in raw_definitions:
        text = item if isinstance(item, str) else str(item)

        # Remove `1`, `4`, ...
        text = JP_MARKER_RE.sub("", text)

        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            # Remove repeated term line
            if line == term:
                continue

            # Format POS line
            if JP_POS_RE.match(line):
                line = f"〘{line.lower()}〙"

            lines.append(line)

        if not lines:
            continue

        normalized.append("\n".join(lines))

    return normalized

