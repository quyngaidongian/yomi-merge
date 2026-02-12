import argparse
import sys
import json
from pathlib import Path
import re
import shutil
from datetime import date
import zipfile

class PreImportValidationError(Exception):
    pass

POS_PREFIX_RE = re.compile(
    r"^\s*(n|v|adj|adv|prep|conj|pron|interj)\.\s*",
    re.IGNORECASE
)

LEMMA_BLOCK_RE = re.compile(r"(\{[^}]+\})")

# TODO: language-specific POS stripping (vi-en dictionaries)

BRACE_BLOCK_RE = re.compile(r"(\s*\{[^}]+\})")

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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build a Yomitan dictionary by merging Dict1 structure with Dict2 definitions."
    )
    parser.add_argument(
        "dict1",
        type=Path,
        help="Path to Dict1 directory (authority dictionary)"
    )
    parser.add_argument(
        "dict2",
        type=Path,
        help="Path to Dict2 directory (definition source)"
    )
    parser.add_argument(
        "output",
        type=Path,
        help="Path to output directory"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=10_000,
        help="Maximum number of entries per term_bank file (default: 10000)"
    )
    parser.add_argument(
        "--title",
        type=str,
        help="Override dictionary title in index.json"
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Do not create zip file (leave output directory only)"
    )
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="Disable definition normalization (use raw definitions from Dict2)"
    )
    parser.add_argument(
        "--copy-reading",
        action="store_true",
        help="Copy reading (index 1) from Dict1 when building Dict2-only entries"
    )



    args = parser.parse_args()

    # Validate Dict1
    if not args.dict1.exists() or not args.dict1.is_dir():
        sys.exit(f"Error: Dict1 path is not a valid directory: {args.dict1}")

    # Validate Dict2
    if not args.dict2.exists() or not args.dict2.is_dir():
        sys.exit(f"Error: Dict2 path is not a valid directory: {args.dict2}")

    # Prepare output directory
    if args.output.exists():
        if not args.output.is_dir():
            sys.exit(f"Error: Output path exists but is not a directory: {args.output}")
    else:
        args.output.mkdir(parents=True, exist_ok=True)

    return args

def scan_dictionary(dir_path: Path):
    """
    Scan a Yomitan dictionary directory and locate required files.

    Returns:
        {
            "index": Path,
            "tag_banks": list[Path],
            "term_banks": list[Path]
        }
    """
    index_path = dir_path / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"Missing index.json in {dir_path}")

    tag_banks = sorted(dir_path.glob("tag_bank_*.json"))
    term_banks = sorted(dir_path.glob("term_bank_*.json"))

    if not term_banks:
        raise FileNotFoundError(f"No term_bank_*.json found in {dir_path}")

    return {
        "index": index_path,
        "tag_banks": tag_banks,
        "term_banks": term_banks,
    }

def iter_dict1_entries(dict1_files):
    """
    Iterate over all term entries in Dict1 term_bank files.

    Yields:
        tuple (entry, source_file)
        - entry: list (length 8)
        - source_file: Path of term_bank file
    """
    term_banks = dict1_files["term_banks"]

    for term_bank_path in term_banks:
        with term_bank_path.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in {term_bank_path}: {e}")

            if not isinstance(data, list):
                raise ValueError(f"{term_bank_path} does not contain a JSON array")

            for entry in data:
                # Basic structural validation (lightweight)
                if not isinstance(entry, list) or len(entry) != 8:
                    raise ValueError(
                        f"Invalid term entry in {term_bank_path}: {entry}"
                    )

                yield entry, term_bank_path

def iter_dict2_entries(dict2_files):
    """
    Iterate over all term entries in Dict2 term_bank files.

    Yields:
        entry: list (length 8)
    """
    term_banks = dict2_files["term_banks"]

    for term_bank_path in term_banks:
        with term_bank_path.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in {term_bank_path}: {e}")

            if not isinstance(data, list):
                raise ValueError(f"{term_bank_path} does not contain a JSON array")

            for entry in data:
                if not isinstance(entry, list) or len(entry) != 8:
                    raise ValueError(
                        f"Invalid term entry in {term_bank_path}: {entry}"
                    )

                yield entry


def is_non_lemma(entry):
    """
    Determine whether a term entry is a non-lemma entry.

    Rule:
    - entry[2] == "non-lemma"  -> non-lemma
    - otherwise               -> lemma
    """
    return entry[2] == "non-lemma"

def index_dict1(dict1_files):
    """
    Build indexes for Dict1.

    Returns:
        lemma_index: dict[str, list[entry]]
        nonlemma_index: dict[str, list[entry]]
        nonlemma_by_term: dict[str, entry]
        lemma_of_term: dict[str, str]
    """
    lemma_index = {}
    nonlemma_index = {}
    nonlemma_by_term = {}
    lemma_of_term = {}

    for entry, _src in iter_dict1_entries(dict1_files):
        term = entry[0]

        if is_non_lemma(entry):
            # entry[5] = [[lemma, [tags...]]]
            try:
                redirect = entry[5][0][0]
            except Exception:
                raise ValueError(f"Invalid non-lemma structure: {entry}")

            lemma = redirect

            nonlemma_index.setdefault(lemma, []).append(entry)
            nonlemma_by_term[term] = entry
            lemma_of_term[term] = lemma
        else:
            lemma = term
            lemma_index.setdefault(lemma, []).append(entry)
            lemma_of_term[term] = lemma

    return (
        lemma_index,
        nonlemma_index,
        nonlemma_by_term,
        lemma_of_term,
    )


def index_dict2(dict2_files, normalize=True):
    """
    Build lemma set and definition map for Dict2.

    Returns:
        dict2_lemmas: set[str]
        dict2_definitions: dict[str, list[str]]
    """
    dict2_lemmas = set()
    dict2_definitions = {}

    for entry in iter_dict2_entries(dict2_files):
        term = entry[0]
        raw_definitions = entry[5]

        if normalize:
            definitions = normalize_definitions(raw_definitions, term)
        else:
            definitions = list(raw_definitions)

        if term in dict2_definitions:
            # Merge definitions instead of dropping
            dict2_definitions[term].extend(definitions)
        else:
            dict2_lemmas.add(term)
            dict2_definitions[term] = definitions

    return dict2_lemmas, dict2_definitions

def merge_entries_from_dict2(
    dict2_lemmas,
    dict2_definitions,
    lemma_index,
    nonlemma_by_term,
    lemma_of_term,
    copy_reading=False,
):
    """
    Build merged entries driven strictly by Dict2 terms.
    """
    merged_entries = []

    for term in sorted(dict2_lemmas):
        definitions = dict2_definitions[term]

        # Case 1: term is lemma in Dict1 → merge Dict1 metadata
        if term in lemma_index:
            for entry in lemma_index[term]:
                new_entry = list(entry)
                new_entry[5] = definitions
                merged_entries.append(new_entry)
            continue

        # Case 2: term is non-lemma in Dict1 → keep Dict2 as-is
        if term in nonlemma_by_term:
            entry = [
                term,           # term
                "",             # reading
                "",             # tags
                "",             # rules
                0,              # score
                definitions,    # definitions
                0,              # sequence
                "",             # term tags
            ]
            merged_entries.append(entry)
            continue

        # Case 3: Dict2-only entry
        reading = ""

        if copy_reading:
            # Priority 1: same-term non-lemma in Dict1
            if term in nonlemma_by_term:
                reading = nonlemma_by_term[term][1]
            else:
                # Priority 2: lemma in Dict1
                lemma = lemma_of_term.get(term)
                if lemma and lemma in lemma_index:
                    reading = lemma_index[lemma][0][1]

        entry = [
            term,
            reading,
            "",
            "",
            0,
            definitions,
            0,
            "",
        ]
        merged_entries.append(entry)

    return merged_entries

def collect_nonlemma_redirects(nonlemma_index, dict2_lemmas):
    """
    Collect Dict1 non-lemma entries whose redirect target exists in Dict2.
    Used only to support deinflection.
    """
    redirects = []

    for lemma, nonlemmas in nonlemma_index.items():
        if lemma not in dict2_lemmas:
            continue

        for entry in nonlemmas:
            redirects.append(list(entry))

    return redirects


def chunk_entries(entries, chunk_size=10_000):
    """
    Yield chunks of entries with at most chunk_size elements.

    Args:
        entries: list of term entries
        chunk_size: int

    Yields:
        list of term entries
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    for i in range(0, len(entries), chunk_size):
        yield entries[i:i + chunk_size]

def write_term_banks(chunks, output_dir):
    """
    Write term_bank_*.json files to output directory.

    Args:
        chunks: iterable of list[entry]
        output_dir: Path
    """
    for idx, chunk in enumerate(chunks, start=1):
        filename = f"term_bank_{idx}.json"
        path = output_dir / filename

        with path.open("w", encoding="utf-8") as f:
            json.dump(chunk, f, ensure_ascii=False, indent=2)

def copy_tag_banks(dict1_dir, output_dir):
    """
    Copy all tag_bank_*.json files from dict1 to output directory.

    Args:
        dict1_dir: Path
        output_dir: Path
    """
    for path in dict1_dir.glob("tag_bank_*.json"):
        target = output_dir / path.name
        shutil.copyfile(path, target)

def build_index_json(dict1_dir, dict2_dir, output_dir, title_override=None):
    """
    Build index.json for output dictionary.

    Rules:
    - Only keep keys present in Dict1/index.json
    - Prefer values from Dict2/index.json when available
    - Always set:
        - sequenced = True
        - revision = YYYY.MM.DD (build date)

    Args:
        dict1_dir: Path
        dict2_dir: Path
        output_dir: Path
    """
    index1_path = dict1_dir / "index.json"
    index2_path = dict2_dir / "index.json"

    with index1_path.open("r", encoding="utf-8") as f:
        index1 = json.load(f)

    with index2_path.open("r", encoding="utf-8") as f:
        index2 = json.load(f)

    output_index = {}

    for key in index1.keys():
        if key in index2:
            output_index[key] = index2[key]
        else:
            output_index[key] = index1[key]

    # Forced fields
    output_index["sequenced"] = True
    output_index["revision"] = date.today().strftime("%Y.%m.%d")

    output_path = output_dir / "index.json"

    if title_override:
        output_index["title"] = title_override

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output_index, f, ensure_ascii=False, indent=2)

def zip_output_directory(output_dir, zip_path):
    """
    Zip output directory into a Yomitan-importable .zip file.

    Args:
        output_dir: Path
        zip_path: Path (should end with .zip)
    """
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in output_dir.iterdir():
            if path.is_file():
                zf.write(path, arcname=path.name)

def validate_output_directory(output_dir):
    """
    Validate output directory before zipping / importing into Yomitan.

    Checks:
    - index.json exists and is valid JSON
    - at least one term_bank_*.json exists
    - all JSON files can be loaded

    Args:
        output_dir: Path

    Raises:
        PreImportValidationError
    """
    index_path = output_dir / "index.json"
    if not index_path.exists():
        raise PreImportValidationError("Missing index.json in output directory")

    term_banks = list(output_dir.glob("term_bank_*.json"))
    if not term_banks:
        raise PreImportValidationError("No term_bank_*.json files found in output directory")

    # Validate all JSON files
    for path in output_dir.iterdir():
        if path.is_file() and path.suffix == ".json":
            try:
                with path.open("r", encoding="utf-8") as f:
                    json.load(f)
            except Exception as e:
                raise PreImportValidationError(
                    f"Invalid JSON file: {path.name} ({e})"
                )

def sanity_check_redirects(entries):
    """
    Ensure all non-lemma redirects point to existing terms.

    Assumes:
    - Only Dict1 non-lemma entries have entry[2] == "non-lemma"
    - entry[5] schema for non-lemma: [[lemma, [tags...]]]
    """
    terms = {entry[0] for entry in entries}

    for entry in entries:
        if entry[2] != "non-lemma":
            continue

        # Validate redirect structure safely
        redirects = entry[5]
        if (
            not isinstance(redirects, list)
            or not redirects
            or not isinstance(redirects[0], list)
            or len(redirects[0]) < 1
            or not isinstance(redirects[0][0], str)
        ):
            raise ValueError(
                f"Invalid non-lemma redirect structure for '{entry[0]}': {entry[5]}"
            )

        redirect_target = redirects[0][0]

        if redirect_target not in terms:
            raise ValueError(
                f"Invalid redirect: '{entry[0]}' → '{redirect_target}'"
            )


def cleanup_output_dir(output_dir):
    """
    Remove all files in output directory before building.
    Keeps the directory itself.
    """
    if not output_dir.exists():
        output_dir.mkdir(parents=True)
        return

    for path in output_dir.iterdir():
        if path.is_file():
            path.unlink()

def main():
    args = parse_args()
    cleanup_output_dir(args.output)

    # --------------------------------------------------
    # Phase 0: Scan input dictionaries
    # --------------------------------------------------
    try:
        dict1_files = scan_dictionary(args.dict1)
        dict2_files = scan_dictionary(args.dict2)
    except FileNotFoundError as e:
        sys.exit(f"Error: {e}")

    # --------------------------------------------------
    # Phase 1: Load & index Dict1
    # --------------------------------------------------
    (
        lemma_index,
        nonlemma_index,
        nonlemma_by_term,
        lemma_of_term,
    ) = index_dict1(dict1_files)


    # --------------------------------------------------
    # Phase 2: Load & index Dict2 (definitions only)
    # --------------------------------------------------
    dict2_lemmas, dict2_definitions = index_dict2(
        dict2_files,
        normalize=not args.no_normalize
    )

    ## 3.x – Dict2-driven merge

    merged_entries = merge_entries_from_dict2(
        dict2_lemmas,
        dict2_definitions,
        lemma_index,
        nonlemma_by_term,
        lemma_of_term,
        copy_reading=args.copy_reading,
    )

    nonlemma_redirects = collect_nonlemma_redirects(
        nonlemma_index,
        dict2_lemmas,
    )

    all_entries = merged_entries + nonlemma_redirects
    all_entries.sort(key=lambda e: e[0])

    sanity_check_redirects(all_entries)

    chunks = list(
        chunk_entries(
            all_entries,
            chunk_size=args.chunk_size
        )
    )

    print("Total merged entries:", len(all_entries))
    print("Total term_bank files:", len(chunks))

    write_term_banks(chunks, args.output)

    # --------------------------------------------------
    # Phase 5: Metadata (tags + index)
    # --------------------------------------------------
    copy_tag_banks(args.dict1, args.output)

    build_index_json(
        args.dict1,
        args.dict2,
        args.output,
        title_override=args.title
    )

    # --------------------------------------------------
    # Pre-import validation
    # --------------------------------------------------
    validate_output_directory(args.output)

    # --------------------------------------------------
    # Phase 6: Zip (optional)
    # --------------------------------------------------
    if not args.no_zip:
        zip_path = args.output.with_suffix(".zip")
        zip_output_directory(args.output, zip_path)


if __name__ == "__main__":
    main()
