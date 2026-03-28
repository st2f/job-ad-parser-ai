from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RAW_DIR = ROOT_DIR / "jobs" / "raw"
DEFAULT_PROCESSED_DIR = ROOT_DIR / "jobs" / "processed"
PROMPT_PATH = ROOT_DIR / "src" / "prompt.md"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
DEFAULT_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
FREE_TEXT_FIELDS = (
    "summary",
    "requirements",
    "responsibilities",
    "benefits",
    "hiring_process",
    "confidence_notes",
)
LATIN_SCRIPT_LANGUAGES = {
    "ca",
    "cs",
    "da",
    "de",
    "en",
    "es",
    "et",
    "fi",
    "fr",
    "hr",
    "hu",
    "id",
    "it",
    "lt",
    "lv",
    "nl",
    "no",
    "pl",
    "pt",
    "ro",
    "sk",
    "sl",
    "sv",
    "tr",
    "vi",
}

JOB_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_language": {"type": ["string", "null"]},
        "company_name": {"type": ["string", "null"]},
        "role_title": {"type": ["string", "null"]},
        "location": {"type": ["string", "null"]},
        "work_model": {
            "type": ["string", "null"],
            "enum": ["remote", "remote_with_travel", "hybrid", "onsite", None],
        },
        "employment_type": {
            "type": ["string", "null"],
            "enum": ["full-time", "part-time", "contract", "internship", "temporary", None],
        },
        "employment_type_raw": {"type": ["string", "null"]},
        "seniority": {"type": ["string", "null"]},
        "salary": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "currency": {"type": ["string", "null"]},
                "min": {"type": ["number", "null"]},
                "max": {"type": ["number", "null"]},
                "period": {
                    "type": ["string", "null"],
                    "enum": ["hour", "day", "month", "year", None],
                },
                "raw": {"type": ["string", "null"]},
            },
            "required": ["currency", "min", "max", "period", "raw"],
        },
        "tech_stack": {
            "type": "array",
            "items": {"type": "string"},
        },
        "responsibilities": {
            "type": "array",
            "items": {"type": "string"},
        },
        "requirements": {
            "type": "array",
            "items": {"type": "string"},
        },
        "nice_to_have": {
            "type": "array",
            "items": {"type": "string"},
        },
        "benefits": {
            "type": "array",
            "items": {"type": "string"},
        },
        "summary": {"type": "string"},
        "application_url": {"type": ["string", "null"]},
        "hiring_process": {"type": ["string", "null"]},
        "confidence_notes": {"type": "string"},
    },
    "required": [
        "source_language",
        "company_name",
        "role_title",
        "location",
        "work_model",
        "employment_type",
        "employment_type_raw",
        "seniority",
        "salary",
        "tech_stack",
        "responsibilities",
        "requirements",
        "nice_to_have",
        "benefits",
        "summary",
        "application_url",
        "hiring_process",
        "confidence_notes",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize raw job ads into structured markdown via the OpenAI Responses API."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Specific raw job ad files to process. If omitted, no files are processed.",
    )
    parser.add_argument(
        "--raw-dir",
        default=str(DEFAULT_RAW_DIR),
        help="Directory containing raw job ads.",
    )
    parser.add_argument(
        "--processed-dir",
        default=str(DEFAULT_PROCESSED_DIR),
        help="Directory where processed markdown files are written.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenAI model to use. Default: {DEFAULT_MODEL}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw_dir = Path(args.raw_dir).resolve()
    processed_dir = Path(args.processed_dir).resolve()
    processed_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Missing OPENAI_API_KEY.", file=sys.stderr)
        return 1

    prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    input_paths = resolve_input_paths(args.inputs, raw_dir)
    if not input_paths:
        print("No raw job ads found to process.")
        return 0

    exit_code = 0
    for input_path in input_paths:
        try:
            output_path = process_job_ad(
                input_path=input_path,
                raw_dir=raw_dir,
                processed_dir=processed_dir,
                prompt=prompt,
                model=args.model,
                api_key=api_key,
            )
            print(f"Processed {input_path} -> {output_path}")
        except Exception as exc:  # noqa: BLE001
            exit_code = 1
            print(f"Failed to process {input_path}: {exc}", file=sys.stderr)

    return exit_code


def resolve_input_paths(inputs: list[str], raw_dir: Path) -> list[Path]:
    if inputs:
        paths = [Path(value).resolve() for value in inputs]
    else:
        paths = []

    supported_suffixes = {".md", ".markdown", ".txt"}
    filtered = [path for path in paths if path.suffix.lower() in supported_suffixes]
    return sorted(dict.fromkeys(filtered))


def process_job_ad(
    *,
    input_path: Path,
    raw_dir: Path,
    processed_dir: Path,
    prompt: str,
    model: str,
    api_key: str,
) -> Path:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    raw_text = input_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        raise ValueError("Input file is empty.")

    extracted = call_openai(prompt=prompt, raw_text=raw_text, model=model, api_key=api_key)
    validate_unicode_fields(extracted)

    generated_at = datetime.now(timezone.utc)
    relative_source = safe_relative_path(input_path, ROOT_DIR)
    output_name = build_output_name(input_path.stem, processed_dir)
    output_path = processed_dir / output_name
    output_path.write_text(
        render_output_markdown(
            extracted=extracted,
            source_path=relative_source,
            source_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            generated_at=generated_at,
            model=model,
        ),
        encoding="utf-8",
    )
    return output_path


def call_openai(*, prompt: str, raw_text: str, model: str, api_key: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "top_p": 0.4,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Normalize this raw job ad into the provided schema.\n\n"
                            f"{raw_text}"
                        ),
                    }
                ],
            }
        ],
        "instructions": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "job_ad_extraction",
                "schema": JOB_SCHEMA,
                "strict": True,
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        url=f"{DEFAULT_BASE_URL}/responses",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with request.urlopen(http_request, timeout=120) as response:
            response_body = response.read()
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API error {exc.code}: {details}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Network error while calling OpenAI: {exc.reason}") from exc

    data = json.loads(response_body.decode("utf-8"))
    parsed_text = extract_output_text(data)
    try:
        return json.loads(parsed_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model did not return valid JSON: {parsed_text}") from exc


def extract_output_text(response_json: dict[str, Any]) -> str:
    for item in response_json.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            content_type = content.get("type")
            if content_type == "output_text":
                return content["text"]
            if content_type == "refusal":
                raise RuntimeError(f"Model refused the request: {content['refusal']}")
    raise RuntimeError("No structured output was returned by the model.")


def render_output_markdown(
    *,
    extracted: dict[str, Any],
    source_path: str,
    source_hash: str,
    generated_at: datetime,
    model: str,
) -> str:
    markdown_section = render_job_markdown(extracted)
    front_matter = [
        "---",
        f"source_file: {source_path}",
        f"source_sha256: {source_hash}",
        f"processed_at_utc: {generated_at.isoformat().replace('+00:00', 'Z')}",
        f"model: {model}",
        "schema_version: 1",
        "---",
        "",
    ]
    if markdown_section:
        front_matter.extend([markdown_section, ""])
    front_matter.extend(
        [
            "## Structured Data",
            "",
            "```json",
            json.dumps(extracted, indent=2, ensure_ascii=True, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(front_matter)


def validate_unicode_fields(extracted: dict[str, Any]) -> None:
    source_language = normalize_language_code(extracted.get("source_language"))
    if source_language not in LATIN_SCRIPT_LANGUAGES:
        return

    for field_name in FREE_TEXT_FIELDS:
        value = extracted.get(field_name)
        if isinstance(value, str):
            suspicious = find_unexpected_script_segment(value)
            if suspicious:
                raise_unicode_validation_error(field_name, value, suspicious)
            continue

        if isinstance(value, list):
            for index, item in enumerate(value):
                if not isinstance(item, str):
                    continue
                suspicious = find_unexpected_script_segment(item)
                if suspicious:
                    raise_unicode_validation_error(f"{field_name}[{index}]", item, suspicious)


def normalize_language_code(value: Any) -> str | None:
    if not value:
        return None
    normalized = str(value).strip().lower().replace("_", "-")
    return normalized.split("-", 1)[0] or None


def find_unexpected_script_segment(text: str) -> str | None:
    letters = list(iter_letter_runs(text))
    if not letters:
        return None

    for start, end, script in letters:
        if script == "LATIN":
            continue
        return text[max(0, start - 20) : min(len(text), end + 20)]
    return None


def iter_letter_runs(text: str) -> list[tuple[int, int, str]]:
    runs: list[tuple[int, int, str]] = []
    current_start: int | None = None
    current_script: str | None = None

    for index, char in enumerate(text):
        if not unicodedata.category(char).startswith("L"):
            if current_script is not None and current_start is not None:
                runs.append((current_start, index, current_script))
                current_start = None
                current_script = None
            continue

        script = detect_script(char)
        if current_script == script and current_start is not None:
            continue

        if current_script is not None and current_start is not None:
            runs.append((current_start, index, current_script))
        current_start = index
        current_script = script

    if current_script is not None and current_start is not None:
        runs.append((current_start, len(text), current_script))

    return runs


def detect_script(char: str) -> str:
    try:
        name = unicodedata.name(char)
    except ValueError:
        return "UNKNOWN"

    for script in (
        "LATIN",
        "GREEK",
        "CYRILLIC",
        "ARMENIAN",
        "HEBREW",
        "ARABIC",
        "DEVANAGARI",
        "HIRAGANA",
        "KATAKANA",
        "HANGUL",
        "CJK",
    ):
        if script == "CJK":
            if "CJK UNIFIED IDEOGRAPH" in name:
                return script
            continue
        if script in name:
            return script
    return "OTHER"


def raise_unicode_validation_error(field_name: str, value: str, suspicious: str) -> None:
    raise ValueError(
        "Unicode validation failed in field "
        f"{field_name}:\n"
        f"{value!r}\n"
        f"Suspicious substring: {suspicious!r}\n"
        "Unexpected non-Latin script detected."
    )


def render_job_markdown(extracted: dict[str, Any]) -> str:
    lines: list[str] = []

    title = render_title(extracted)
    if title:
        lines.extend([title, ""])

    application_url = extracted.get("application_url")
    if application_url:
        lines.extend([application_url, ""])

    info_lines = [
        ("Location", extracted.get("location")),
        ("Salary", render_salary(extracted.get("salary"))),
        ("Work model", render_enum_label(extracted.get("work_model"))),
        (
            "Employment type",
            extracted.get("employment_type_raw")
            or render_enum_label(extracted.get("employment_type")),
        ),
        ("Seniority", extracted.get("seniority")),
    ]
    append_key_value_section(lines, "Infos", info_lines)
    append_list_section(lines, "Stack", extracted.get("tech_stack"))
    append_paragraph_section(lines, "Summary", extracted.get("summary"))
    append_list_section(lines, "Requirements", extracted.get("requirements"))
    append_list_section(lines, "Nice to have", extracted.get("nice_to_have"))
    append_list_section(lines, "Responsibilities", extracted.get("responsibilities"))
    append_list_section(lines, "Benefits", extracted.get("benefits"))
    append_paragraph_section(lines, "Hiring process", extracted.get("hiring_process"))

    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def render_title(extracted: dict[str, Any]) -> str:
    role_title = extracted.get("role_title")
    company_name = extracted.get("company_name")
    if role_title and company_name:
        return f"# {role_title} - {company_name}"
    if role_title:
        return f"# {role_title}"
    if company_name:
        return f"# {company_name}"
    return "# Structured Job Ad"


def render_salary(salary: Any) -> str | None:
    if not isinstance(salary, dict):
        return None
    raw = salary.get("raw")
    if raw:
        return str(raw)
    minimum = salary.get("min")
    maximum = salary.get("max")
    currency = salary.get("currency")
    period = salary.get("period")
    parts = [str(value) for value in (minimum, maximum) if value is not None]
    if not parts and not currency and not period:
        return None
    rendered = " - ".join(parts) if parts else ""
    if currency:
        rendered = f"{rendered} {currency}".strip()
    if period:
        rendered = f"{rendered} / {period}".strip()
    return rendered or None


def render_enum_label(value: Any) -> str | None:
    if not value:
        return None
    return str(value).replace("_", " ").replace("-", " ")


def append_key_value_section(
    lines: list[str], heading: str, items: list[tuple[str, Any]]
) -> None:
    rendered_items = [(label, value) for label, value in items if value]
    if not rendered_items:
        return
    lines.extend([f"## {heading}", ""])
    for label, value in rendered_items:
        lines.append(f"- {label}: {value}")
    lines.append("")


def append_list_section(lines: list[str], heading: str, items: Any) -> None:
    if not isinstance(items, list):
        return
    rendered_items = [str(item) for item in items if item]
    if not rendered_items:
        return
    lines.extend([f"## {heading}", ""])
    for item in rendered_items:
        lines.append(f"- {item}")
    lines.append("")


def append_paragraph_section(lines: list[str], heading: str, value: Any) -> None:
    if not value:
        return
    lines.extend([f"## {heading}", "", str(value), ""])


def safe_relative_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def build_output_name(stem: str, processed_dir: Path) -> str:
    del processed_dir
    slug = slugify(stem)
    return f"{slug}.md"


def slugify(value: str) -> str:
    lowered = value.lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return normalized or "job-ad"


if __name__ == "__main__":
    raise SystemExit(main())
