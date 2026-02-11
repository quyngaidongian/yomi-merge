# Yomitan Dictionary Merger CLI

## Overview

This CLI tool builds a Yomitan-compatible dictionary by merging two existing Yomitan dictionaries with complementary roles.

The first dictionary (`Dict1`) is treated as the structural and linguistic authority. It provides part-of-speech tags, deinflection rules, non-lemma entries, and sequence numbers required for proper Yomitan behavior.

The second dictionary (`Dict2`) is treated as the definition authority. It typically originates from other dictionary formats such as MDX or DSL and is converted to Yomitan format using tools like pyglossary. `Dict2` provides the actual glossary content but usually lacks reliable grammatical metadata.

The output is a fully importable Yomitan dictionary (`.zip`) that preserves the linguistic accuracy of `Dict1` while replacing or injecting definitions from `Dict2`.

This tool is designed primarily for building bilingual dictionaries, for example English–Vietnamese, French–Vietnamese, or German–Vietnamese, while retaining correct inflection handling in Yomitan.

## Dictionary Model

The merge process is based on a strict separation of responsibilities.

`Dict1` defines how words behave. It supplies lemma entries, non-lemma entries (such as inflected forms), POS tags, deinflection rules, sequence numbers, and tag banks.

`Dict2` defines what words mean. Only the definition field is taken from `Dict2`. All other fields are ignored.

A term is considered the same word in both dictionaries if and only if the term string (index 0 of a term entry) matches exactly. Reading fields are ignored. `Dict2` is assumed to contain at most one entry per term. If multiple entries exist, only the first one is used.

Only lemmas that exist in both `Dict1` and `Dict2` are included in the output. Non-lemma entries from `Dict1` are included only if their corresponding lemma is present in `Dict2`.

## Output Dictionary Behavior

The resulting dictionary contains:

* All lemma entries from `Dict1` whose term exists in `Dict2`, with definitions replaced by those from `Dict2`.
* All non-lemma entries from `Dict1` that redirect to valid lemmas.
* All tag banks copied verbatim from `Dict1`.
* An index.json file merged from `Dict1` and `Dict2` metadata.

Sequence numbers from `Dict1` are preserved. This allows Yomitan to merge multiple parts of speech for the same term correctly when resultOutputMode is set to "merge".

The output is split into multiple `term_bank_*.json` files with a configurable maximum number of entries per file.

## Usage

Basic usage:

```bash
python3 main.py `Dict1`/ `Dict2`/ output/
```

This command builds a Yomitan dictionary from the two input directories and writes the result to the output directory. A zip file with the same name as the output directory is created by default.

Example:

```bash
python3 main.py oald/ anh_viet/ oald_vi/
```

This produces:

```text
oald_vi/
├── index.json
├── tag_bank_1.json
├── term_bank_1.json
├── term_bank_2.json
└── ...
```

and a file:

```text
oald_vi`.zip`
```

which can be imported directly into Yomitan.

## Command-line Options

### --chunk-size

Controls the maximum number of entries per `term_bank` file.

```bash
--chunk-size 10000
```

The default value is 10000. Smaller values produce more files and may improve import stability for very large dictionaries.

### --title

Overrides the dictionary title in the generated index.json.

```bash
--title "English–Vietnamese (OALD)"
```

Only the title field is overridden. All other metadata follows the merge rules.

### --no-zip

Prevents creation of the zip file. Only the output directory is generated.

```bash
--no-zip
```

This option is useful for debugging, inspecting JSON output, or performing additional processing before packaging.

## Pre-import Validation

Before creating the zip file, the tool performs minimal validation to prevent silent import failures in Yomitan.

It verifies that index.json exists, at least one `term_bank` file is present, and all JSON files in the output directory can be successfully loaded.

If validation fails, the program exits with an error and does not produce a zip file.

## Performance and Scale

The tool is designed to handle dictionaries with tens of thousands to several hundred thousand entries.

All indexing and merging operations are performed in memory. For typical bilingual dictionaries of 50,000 to 200,000 entries, performance and memory usage are acceptable on modern systems.

## Limitations

This tool does not attempt to infer or split parts of speech from `Dict2`. Definitions are treated as opaque text after optional normalization. Linguistic correctness depends entirely on `Dict1`.

Language-specific definition normalization is intentionally kept minimal to preserve generality across different dictionary sources.

## Intended Use Cases

The primary use case is building high-quality bilingual Yomitan dictionaries where:

* `Dict1` provides reliable grammatical structure and inflection handling (E.g: [kaikki-to-yomitan](https://yomidevs.github.io/kaikki-to-yomitan/)).
* `Dict2` provides rich, human-readable definitions in another language (E.g: dictionaries converted from MDX or DSL using [pyglossary](https://github.com/ilius/pyglossary)).

The tool is not intended for creating dictionaries from scratch or for languages that require complex script-specific processing without a suitable `Dict1`.

## Definition Normalization (`normalize_definitions`)

The function `normalize_definitions` is responsible for transforming raw definition text extracted from Dict2 into a clean, consistent format suitable for display in Yomitan.

`Dict2` is typically converted from other dictionary formats such as MDX or DSL using pyglossary. As a result, its definitions often contain artifacts such as leading part-of-speech markers, numbering schemes, inline abbreviations, or formatting conventions that are not ideal for Yomitan’s popup display.

This function acts as a normalization layer between raw converted data and the final dictionary output.

During the indexing phase of `Dict2`, raw definition strings are collected for each term. These raw definitions are then passed through `normalize_definitions` before being merged into Dict1 entries.

Only the definition field (index 5 of a Yomitan term entry) is affected. All grammatical metadata such as POS tags, deinflection rules, and sequence numbers are preserved from Dict1 and are not inferred or modified based on Dict2 content.

### Design Constraints

`normalize_definitions` is intentionally designed to be conservative and language-agnostic by default.

It does not attempt to infer parts of speech, grammatical structure, or semantic grouping beyond simple text normalization. This is a deliberate choice to avoid introducing incorrect linguistic assumptions, especially when working across multiple languages or dictionary sources.

More aggressive normalization, such as POS-aware splitting or language-specific restructuring, should be implemented as separate, optional logic layered on top of this function.

### Typical Normalization Tasks

Depending on the source dictionary, normalization may include removing leading POS markers such as “n.” or “v.”, trimming redundant whitespace, collapsing multiple spaces, stripping numbering prefixes, or splitting compound definition strings into multiple displayable senses.

The exact behavior is expected to vary between dictionary sources. For this reason, `normalize_definitions` is treated as a customizable component rather than a fixed algorithm.

### Extensibility

The function is written to be easily replaced or extended.

If different dictionaries require different normalization strategies, this function can be rewritten or parameterized without affecting the rest of the pipeline. The surrounding code assumes only that the function returns a list of clean definition strings.