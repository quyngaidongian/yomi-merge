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

# TODO: language-specific POS stripping (vi-en dictionaries)

def normalize_definitions(
    raw_definitions,
    term,
    lang="auto",
    source="dict2"
):
    """
    Normalize raw definitions from Dict2 into list[str].

    Default behavior (safe):
    - Ensure list[str]
    - Strip leading term repetition
    - Strip simple POS markers (n., v., adj., ...)
    - Split by ';'
    """

    if not raw_definitions:
        return []

    normalized = []

    for item in raw_definitions:
        if not isinstance(item, str):
            # Unexpected structure → stringify safely
            text = str(item)
        else:
            text = item

        text = text.strip()

        # Remove leading term repetition (e.g. "lead n. ...")
        if text.lower().startswith(term.lower()):
            text = text[len(term):].lstrip(" .:-")

        # Remove POS prefix (very conservative)
        text = POS_PREFIX_RE.sub("", text)

        # Split by semicolon
        parts = [p.strip() for p in text.split(";") if p.strip()]

        normalized.extend(parts)

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

def get_non_lemma_target(entry):
    """
    Get lemma target term from a non-lemma entry.

    Assumes entry is non-lemma.

    Example structure:
    entry[5] == [
        ["abolish", ["past", "participle"]]
    ]
    """
    try:
        return entry[5][0][0]
    except (IndexError, TypeError):
        raise ValueError(f"Invalid non-lemma definition structure: {entry}")

def index_dict1(dict1_files):
    """
    Build lemma and non-lemma index for Dict1.

    Returns:
        lemma_index: dict[str, list[entry]]
        nonlemma_index: dict[str, list[entry]]
    """
    lemma_index = {}
    nonlemma_index = {}

    for entry, _src in iter_dict1_entries(dict1_files):
        term = entry[0]

        if is_non_lemma(entry):
            # non-lemma: redirect target is the lemma
            lemma_target = get_non_lemma_target(entry)

            if lemma_target not in nonlemma_index:
                nonlemma_index[lemma_target] = []
            nonlemma_index[lemma_target].append(entry)
        else:
            # lemma entry
            if term not in lemma_index:
                lemma_index[term] = []
            lemma_index[term].append(entry)

    return lemma_index, nonlemma_index

def index_dict2(dict2_files):
    """
    Build lemma set and normalized definition map for Dict2.

    Returns:
        dict2_lemmas: set[str]
        dict2_definitions: dict[str, list[str]]
    """
    dict2_lemmas = set()
    dict2_definitions = {}

    for entry in iter_dict2_entries(dict2_files):
        term = entry[0]

        if term in dict2_lemmas:
            continue

        raw_definitions = entry[5]
        normalized = normalize_definitions(raw_definitions, term)

        dict2_lemmas.add(term)
        dict2_definitions[term] = normalized

    return dict2_lemmas, dict2_definitions

def select_valid_lemmas(lemma_index, dict2_lemmas):
    """
    Select lemmas that exist in both Dict1 and Dict2.

    Args:
        lemma_index: dict[str, list[entry]] from Dict1
        dict2_lemmas: set[str] from Dict2

    Returns:
        set[str]: valid lemmas for output
    """
    return set(lemma_index.keys()) & dict2_lemmas

def merge_lemma_entries(valid_lemmas, lemma_index, dict2_definitions):
    """
    Merge lemma entries from Dict1 with definitions from Dict2.

    Args:
        valid_lemmas: set[str]
        lemma_index: dict[str, list[entry]]
        dict2_definitions: dict[str, list[str]]

    Returns:
        list[list]: merged lemma entries
    """
    merged = []

    for lemma in sorted(valid_lemmas):
        dict1_entries = lemma_index.get(lemma, [])
        definitions = dict2_definitions[lemma]

        for entry in dict1_entries:
            # Shallow copy is sufficient (we overwrite definitions)
            new_entry = list(entry)
            new_entry[5] = definitions
            merged.append(new_entry)

    return merged

def merge_nonlemma_entries(valid_lemmas, nonlemma_index):
    """
    Collect non-lemma entries whose lemma target exists in valid_lemmas.

    Args:
        valid_lemmas: set[str]
        nonlemma_index: dict[str, list[entry]]

    Returns:
        list[list]: merged non-lemma entries
    """
    merged = []

    for lemma in sorted(valid_lemmas):
        entries = nonlemma_index.get(lemma)
        if not entries:
            continue

        for entry in entries:
            # Shallow copy to avoid mutating Dict1 data
            merged.append(list(entry))

    return merged

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
    lemma_index, nonlemma_index = index_dict1(dict1_files)

    # --------------------------------------------------
    # Phase 2: Load & index Dict2 (definitions only)
    # --------------------------------------------------
    dict2_lemmas, dict2_definitions = index_dict2(dict2_files)

    # --------------------------------------------------
    # Phase 3: Select valid lemmas & merge
    # --------------------------------------------------
    valid_lemmas = select_valid_lemmas(lemma_index, dict2_lemmas)

    merged_lemma_entries = merge_lemma_entries(
        valid_lemmas,
        lemma_index,
        dict2_definitions
    )

    merged_nonlemma_entries = merge_nonlemma_entries(
        valid_lemmas,
        nonlemma_index
    )

    # --------------------------------------------------
    # Phase 4: Chunk & write term banks
    # --------------------------------------------------
    all_merged_entries = merged_lemma_entries + merged_nonlemma_entries

    chunks = list(
        chunk_entries(
            all_merged_entries,
            chunk_size=args.chunk_size
        )
    )

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
    # Phase 7: Pre-import validation
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
