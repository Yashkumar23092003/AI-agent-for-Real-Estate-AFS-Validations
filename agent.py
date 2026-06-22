import os
import re
import json
import base64
import fitz  # PyMuPDF
import tempfile
import datetime
from markitdown import MarkItDown
from openai import OpenAI
from dotenv import load_dotenv
from comparator import run_comparison, validate_extraction

load_dotenv()

MODEL_NAME = "gpt-4o"

_DIR = os.path.dirname(os.path.abspath(__file__))
_SHEET_PROMPT_PATH = os.path.join(_DIR, "afs_sheet_system_prompt.md")
_FIXTURE_PATH = os.path.join(_DIR, "tests", "fixtures", "afs_313_extraction.json")
_PLACEHOLDER_MARKER = "# TODO"

# Maximum characters of AFS text to send to the LLM.
# ~4 chars per token, so 80,000 chars ≈ 20,000 tokens — leaves room for system prompt + images.
MAX_AFS_TEXT_CHARS = 80000

# Maximum file size per upload (15 MB)
MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024


def convert_pdf_to_base64_images(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        base64_images = []
        for page in doc:
            # matrix increases resolution for better OCR by OpenAI
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img_bytes = pix.tobytes("jpeg")
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            base64_images.append(f"data:image/jpeg;base64,{b64}")
        return base64_images
    finally:
        doc.close()

def get_base64_from_bytes(file_bytes: bytes, mime_type: str):
    if "pdf" in mime_type:
        return convert_pdf_to_base64_images(file_bytes)
    else:
        b64 = base64.b64encode(file_bytes).decode("utf-8")
        return [f"data:{mime_type};base64,{b64}"]


def validate_file_sizes(afs_bytes: bytes, aadhaar_list: list, pan_list: list):
    """Validate that no uploaded file exceeds the maximum allowed size."""
    if len(afs_bytes) > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"AFS file is too large ({len(afs_bytes) / (1024*1024):.1f} MB). Maximum allowed is {MAX_FILE_SIZE_BYTES / (1024*1024):.0f} MB.")
    for i, item in enumerate(aadhaar_list):
        if len(item["bytes"]) > MAX_FILE_SIZE_BYTES:
            raise ValueError(f"Aadhaar file #{i+1} is too large. Maximum allowed is {MAX_FILE_SIZE_BYTES / (1024*1024):.0f} MB.")
    for i, item in enumerate(pan_list):
        if len(item["bytes"]) > MAX_FILE_SIZE_BYTES:
            raise ValueError(f"PAN file #{i+1} is too large. Maximum allowed is {MAX_FILE_SIZE_BYTES / (1024*1024):.0f} MB.")


def _extract_last_json_block(text: str) -> dict | None:
    """Extract the LAST ```json ... ``` block from the response to avoid false matches."""
    json_blocks = re.findall(r'```json\s*(.*?)```', text, re.DOTALL)
    if json_blocks:
        return json.loads(json_blocks[-1].strip())
    return None


def _extract_afs_text(afs_bytes: bytes, afs_filename: str = "afs_document.pdf") -> tuple:
    """
    Extracts text from an AFS PDF. Returns (afs_text: str, afs_truncated: bool).
    Uses MarkItDown with PyMuPDF fallback. Saves the result to extracted_markdowns/.
    """
    afs_text = ""
    markdown_conversion_success = False
    conversion_error_msg = ""
    tmp_file_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(afs_bytes)
            tmp_file_path = tmp_file.name

        try:
            md = MarkItDown()
            result = md.convert(tmp_file_path)
            afs_text = result.text_content
            if afs_text.strip():
                markdown_conversion_success = True
        except Exception as e:
            conversion_error_msg = str(e)
            print(f"Warning: MarkItDown conversion failed, falling back to PyMuPDF: {e}")
    finally:
        if tmp_file_path and os.path.exists(tmp_file_path):
            os.remove(tmp_file_path)

    if not markdown_conversion_success or not afs_text.strip():
        try:
            doc = fitz.open(stream=afs_bytes, filetype="pdf")
            try:
                extracted_pages = []
                for page_num, page in enumerate(doc):
                    extracted_pages.append(f"--- PAGE {page_num + 1} ---\n{page.get_text()}")
                afs_text = "\n\n".join(extracted_pages)
            finally:
                doc.close()
        except Exception as pdf_err:
            afs_text = (
                f"[ERROR CONVERTING AFS TO TEXT. "
                f"MarkItDown error: {conversion_error_msg}. PyMuPDF error: {str(pdf_err)}]"
            )

    afs_truncated = False
    if len(afs_text) > MAX_AFS_TEXT_CHARS:
        afs_text = afs_text[:MAX_AFS_TEXT_CHARS]
        afs_truncated = True

    try:
        output_dir = "extracted_markdowns"
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = os.path.splitext(afs_filename)[0]
        ext = ".md" if markdown_conversion_success else ".txt"
        md_filepath = os.path.join(output_dir, f"{timestamp}_{base_name}{ext}")
        with open(md_filepath, "w", encoding="utf-8") as md_file:
            md_file.write(afs_text)
    except Exception as save_err:
        print(f"Warning: Could not save extracted text file: {save_err}")

    return afs_text, afs_truncated


def verify_documents(afs_bytes: bytes, afs_mime: str, aadhaar_list: list, pan_list: list, afs_filename: str = "afs_document.pdf", afs_text: str | None = None, afs_truncated: bool = False):
    """
    Passes the documents to OpenAI (gpt-4o) to perform the KYC cross-verification
    based strictly on the system prompt rules.

    If `afs_text` is provided, the AFS PDF text extraction is skipped and the
    supplied text is used as-is (so a caller running multiple checks can extract
    once and share the result). `afs_truncated` should accompany a supplied
    `afs_text` to render the truncation note correctly.
    """
    # Validate file sizes before processing
    validate_file_sizes(afs_bytes, aadhaar_list, pan_list)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set in the environment.")

    # Create client with a timeout to prevent indefinite hangs
    client = OpenAI(api_key=api_key, timeout=120.0)

    # Read the system prompt from the file we saved
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        prompt_path = os.path.join(current_dir, "system_prompt.md")
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except Exception as e:
        print(f"Warning: Could not read system_prompt.md: {e}")
        system_instruction = "You are a KYC Verification Agent. Verify that AFS matches KYC documents exactly."

    if afs_text is None:
        afs_text, afs_truncated = _extract_afs_text(afs_bytes, afs_filename)

    truncation_note = ""
    if afs_truncated:
        truncation_note = "\n    ⚠️ NOTE: The AFS text was very long and has been truncated. Some later pages may be missing. Focus on verifying the fields that are present.\n"

    prompt_text = f"""
    Please perform the full KYC verification for the provided documents.

    Document 1 is the Agreement for Sale (AFS). Below is the text content extracted from the AFS PDF:
    --- START AFS TEXT ---
    {afs_text}
    --- END AFS TEXT ---
    {truncation_note}
    We have provided the Aadhaar Card(s) and PAN Card(s) as images below.
    Note: Multiple Aadhaar Cards and/or PAN Cards may be provided for joint/co-applicants.

    Output the final Verification Report EXACTLY as specified in your instructions. Additionally, please return a JSON block at the very end of your response enclosed in ```json ... ``` with the following keys. DO NOT output any conversational text (like "Here is the JSON output") before the JSON block.
    - "status": "MATCH" or "MISMATCH" (Overall status)
    - "buyer_name": "Full name of the primary buyer"
    - "project_name": "Name of the project from AFS"
    - "unit_number": "Unit/Flat number from AFS"
    - "afs_date": "Date of the agreement"
    - "mismatches_text": "A brief summary of what mismatched (or 'None'). If there are missing KYC documents for co-applicants, output the exact warning string here."
    """

    messages = [
        {"role": "system", "content": system_instruction},
    ]

    # 2. Append the actual Base64 document payloads
    content_array = [{"type": "text", "text": prompt_text}]

    # Convert all Aadhaar and PAN documents to base64 images and append to content array
    for item in aadhaar_list:
        for b64 in get_base64_from_bytes(item["bytes"], item["mime"]):
            content_array.append({"type": "image_url", "image_url": {"url": b64}})
    for item in pan_list:
        for b64 in get_base64_from_bytes(item["bytes"], item["mime"]):
            content_array.append({"type": "image_url", "image_url": {"url": b64}})

    messages.append({"role": "user", "content": content_array})

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=0.1,
        max_tokens=4096
    )

    text_response = response.choices[0].message.content

    # Extract JSON from the response — use the LAST json block to avoid false matches
    json_data = {}
    try:
        parsed = _extract_last_json_block(text_response)
        if parsed:
            json_data = parsed
            # Remove the last JSON block from the report shown to the user
            # Find the last ```json...``` and strip it
            last_json_start = text_response.rfind("```json")
            if last_json_start != -1:
                report_text = text_response[:last_json_start].strip()
                # Remove conversational filler like "Here is the JSON output:"
                lines = report_text.split('\n')
                while lines and ('json' in lines[-1].lower() or lines[-1].strip() == '' or lines[-1].strip() == '```'):
                    lines.pop()
                report_text = '\n'.join(lines).strip()
            else:
                report_text = text_response
        else:
            report_text = text_response
            # Fallback parsing
            json_data = {
                "status": "MISMATCH" if "❌ MISMATCH DETECTED" in report_text else "MATCH",
                "buyer_name": "Unknown",
                "project_name": "Unknown",
                "unit_number": "Unknown",
                "afs_date": "Unknown",
                "mismatches_text": "Failed to parse JSON. Please check report."
            }
    except Exception as e:
        report_text = text_response
        json_data = {"status": "MISMATCH", "error": str(e)}

    return report_text, json_data


# ── AFS ↔ Sheet verification ──────────────────────────────────────────────────

def _sheet_prompt_is_placeholder() -> bool:
    """Returns True if afs_sheet_system_prompt.md has not been filled in yet."""
    try:
        with open(_SHEET_PROMPT_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        return content.strip().startswith(_PLACEHOLDER_MARKER)
    except FileNotFoundError:
        return True


def _quick_extract_unit_no(afs_text: str) -> str | None:
    """
    Regex pre-extraction of unit number from AFS text.
    Used to fetch the sheet row before the LLM call (since the prompt needs the sheet row).
    """
    for pat in [
        r'Unit\s+bearing\s+No\.\s*(\w+)',
        r'Unit\s+No\.\s*(\w+)',
        r'unit\s+no\.?\s*:?\s*(\w+)',
    ]:
        m = re.search(pat, afs_text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().rstrip('.,;')
            if candidate:
                return candidate
    return None


def _format_sheet_row_for_llm(sheet_row: dict) -> str:
    """Format a sheet row dict as a readable table for the LLM."""
    lines = []
    for key, val in sheet_row.items():
        if isinstance(val, list):
            for i, v in enumerate(val, 1):
                lines.append(f"  {key} [{i}]: {v}")
        else:
            lines.append(f"  {key}: {val}")
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict | None:
    """
    Extracts the first complete JSON object from a text response.
    Handles both markdown code fences and bare JSON (the prompt appends a VERDICT line).
    """
    # Try ```json ... ``` block first
    parsed = _extract_last_json_block(text)
    if parsed:
        return parsed

    # Find the first { and match its closing }
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# Maps the prompt's "field" names (afs_sheet_system_prompt.md, section A) to
# the extraction-contract keys comparator.py knows how to compare.
SHEET_FIELD_MAP = {
    "AGREEMENT_VALUE": "agreement_value",
    "UNIT_NUMBER": "unit_number",
    "FLOOR": "floor",
    "AREA_SQM": "area_sqm",
    "AREA_SQFT": "area_sqft",
    "APPLICANT_NAME": "applicant_name",
    "APPLICANT_PAN": "applicant_pan",
    "APPLICANT_EMAIL": "applicant_email",
    "PARKING_NO": "parking_no",
    "PARKING_LEVEL": "parking_level",
    "PARKING_CONF": "parking_conf",
    "PARKING_LENGTH": "parking_length",
    "PARKING_WIDTH": "parking_width",
    "PARKING_HEIGHT": "parking_height",
    "PARKING_TOTAL_AREA": "parking_total_area",
    "SHARE_CERT_NO": "share_cert_no",
    "SHARE_FROM": "share_from",
    "SHARE_TO": "share_to",
    "TOTAL_SHARES": "total_shares",
    "LEGAL_CHARGES": "legal_charges",
}


def _is_new_prompt_format(parsed: dict) -> bool:
    """
    Detects a structured prompt output. Accepts either schema:
      - the current afs_sheet_system_prompt.md output keyed on 'fields', or
      - the extended schema keyed on 'sheet_comparable_fields'.
    """
    return (
        isinstance(parsed.get("fields"), list)
        or isinstance(parsed.get("sheet_comparable_fields"), list)
    )


def _adapt_new_prompt_format(parsed: dict) -> dict:
    """
    Converts the prompt's output JSON into the extraction contract format
    that comparator.py and validate_extraction() expect.

    The LLM's own comparison results (MATCH/MISMATCH/etc.) are discarded for
    every sheet-comparable field — Python is authoritative. Only the AFS
    extraction data (occurrences, values, internal status) is kept. The LLM's
    derived-check fields, info-only fields, and confidence flags are carried
    through unchanged for display only — they don't affect the verdict.
    """
    contract: dict = {
        "afs_meta": {
            "buyer_name": parsed.get("buyer_name", "Unknown"),
            "project_name": parsed.get("project_name", "Unknown"),
            "afs_date": parsed.get("afs_date", "Unknown"),
        },
        "llm_derived_check_fields": parsed.get("derived_check_fields", []),
        "llm_info_only_fields": parsed.get("info_only_fields", []),
        "llm_out_of_scope_sheet_columns": parsed.get("out_of_scope_sheet_columns", []),
        "llm_low_confidence_or_unverifiable": parsed.get("low_confidence_or_unverifiable", []),
        "llm_internal_sanity_checks": parsed.get("internal_sanity_checks", {}),
    }

    any_discrepancy = False
    any_missing = False

    # Accept either the extended 'sheet_comparable_fields' schema or the current
    # afs_sheet_system_prompt.md 'fields' schema. The occurrence-key fallbacks
    # below (anchor/location, raw/raw_text, normalized/value) handle both.
    field_list = parsed.get("sheet_comparable_fields")
    if not isinstance(field_list, list):
        field_list = parsed.get("fields", [])

    for f in field_list:
        fname = f.get("field", "").upper()
        contract_key = SHEET_FIELD_MAP.get(fname)
        if not contract_key:
            continue

        internal_status = f.get("afs_internal_status", "INTERNAL_OK")
        raw_occurrences = f.get("afs_occurrences", [])

        # Normalise occurrence objects to extraction contract shape
        occurrences = [
            {
                "location": occ.get("anchor", occ.get("location", "")),
                "raw_text": occ.get("raw", occ.get("raw_text", "")),
                "value": occ.get("normalized", occ.get("value", "")),
                "confidence": occ.get("confidence", ""),
            }
            for occ in raw_occurrences
        ]

        # Build distinct_values
        if internal_status == "INTERNAL_OK":
            afs_value_used = f.get("afs_value_used", "")
            if not afs_value_used and occurrences:
                afs_value_used = occurrences[0]["value"]
            distinct_values = [afs_value_used] if afs_value_used else []
        else:
            seen = {}
            for occ in occurrences:
                v = occ.get("value", "")
                if v and v not in seen:
                    seen[v] = True
            distinct_values = list(seen.keys())
            any_discrepancy = True

        if not distinct_values:
            any_missing = True

        field_entry: dict = {
            "occurrences": occurrences,
            "distinct_values": distinct_values,
            "internal_status": internal_status,
        }

        if contract_key == "agreement_value":
            sanity = parsed.get("internal_sanity_checks", {})
            fvw = sanity.get("value_figure_vs_words", "OK")
            field_entry["figure_vs_words"] = "OK" if fvw == "OK" else f"DISCREPANCY: {fvw}"

        contract[contract_key] = field_entry

    # Fill in the required core fields if the LLM omitted them
    for key in ("unit_number", "agreement_value", "area_sqm", "area_sqft"):
        if key not in contract:
            contract[key] = {
                "occurrences": [],
                "distinct_values": [],
                "internal_status": "INTERNAL_OK",
            }
            any_missing = True

    if any_discrepancy:
        contract["extraction_confidence"] = "LOW"
    elif any_missing:
        contract["extraction_confidence"] = "MEDIUM"
    else:
        contract["extraction_confidence"] = "HIGH"

    return contract


# Recital letters and clause numbers actually referenced as anchors in
# afs_sheet_system_prompt.md (Section A/B/C). Everything else in a "Superb
# Altura" AFS — background recitals, boilerplate reps/warranties, the First
# Schedule — carries no field anchor and is safe to drop before sending to the
# LLM, purely to cut input tokens.
_KEEP_RECITAL_LETTERS = set("QRSTUXYZ")
_KEEP_CLAUSE_NUMBERS = {"1", "6", "15"}


def _filter_lettered_block(block: str, keep_letters: set) -> str:
    starts = list(re.finditer(r'(?m)^([A-Z])\.\s', block))
    found = {m.group(1) for m in starts}
    if len(starts) < 10 or not keep_letters.issubset(found):
        raise ValueError(f"recital structure mismatch: found={sorted(found)}")
    kept = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(block)
        if m.group(1) in keep_letters:
            kept.append(block[m.start():end])
    return "".join(kept)


def _filter_numbered_block(block: str, keep_numbers: set) -> str:
    starts = list(re.finditer(r'(?m)^(\d{1,2})\.\s', block))
    found = {m.group(1) for m in starts}
    if len(starts) < 8 or not keep_numbers.issubset(found):
        raise ValueError(f"clause structure mismatch: found={sorted(found)}")
    kept = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(block)
        if m.group(1) in keep_numbers:
            kept.append(block[m.start():end])
    return "".join(kept)


def _filter_afs_text_for_llm(afs_text: str) -> str:
    """
    Drops AFS sections that carry no field anchor referenced anywhere in
    afs_sheet_system_prompt.md (Section A/B/C tables): background recitals
    (land/society history), boilerplate representations/warranties clauses,
    and the First Schedule (land boundary description). Every anchor quoted in
    the prompt lives in the page-1 header, recitals Q/R/S/T/U/X/Y/Z, clauses
    1/6/15, or from the Second Schedule (Parts A/B/C) onward — all preserved
    unfiltered. This is a token-cost optimization only.

    Falls back to the original, unfiltered text if the expected recital/clause
    numbering isn't found (e.g. a differently structured document) — nothing
    is ever silently dropped on a document that doesn't match this structure.
    """
    try:
        whereas = re.search(r'(?m)^WHEREAS:?\s*$', afs_text)
        deed = re.search(r'NOW THIS DEED WITH?NESSETH', afs_text)
        second_schedule = re.search(r'SECOND SCHEDULE HEREINABOVE REFERRED TO', afs_text)
        if not (whereas and deed and second_schedule):
            raise ValueError("expected section headings not found")

        header = afs_text[:whereas.end()]
        recitals_block = afs_text[whereas.end():deed.start()]
        clauses_block = afs_text[deed.start():second_schedule.start()]
        schedules_onward = afs_text[second_schedule.start():]

        filtered = (
            header
            + _filter_lettered_block(recitals_block, _KEEP_RECITAL_LETTERS)
            + _filter_numbered_block(clauses_block, _KEEP_CLAUSE_NUMBERS)
            + schedules_onward
        )
    except Exception:
        return afs_text

    if 0 < len(filtered) < len(afs_text):
        return filtered
    return afs_text


def _call_sheet_extraction_llm(afs_text: str, sheet_row: dict) -> dict:
    """
    Sends AFS text + sheet row to GPT-4o using the configured prompt.
    Detects the response format and returns a validated extraction contract.
    """
    with open(_SHEET_PROMPT_PATH, "r", encoding="utf-8") as f:
        sheet_prompt = f.read()

    afs_text = _filter_afs_text_for_llm(afs_text)
    sheet_row_text = _format_sheet_row_for_llm(sheet_row)
    user_prompt = (
        f"AFS_TEXT:\n{afs_text[:MAX_AFS_TEXT_CHARS]}\n\n"
        f"SHEET_ROW:\n{sheet_row_text}"
    )

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set in the environment.")

    client = OpenAI(api_key=api_key, timeout=180.0)
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": sheet_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=16384,  # gpt-4o's hard output ceiling — the new prompt's schema needs ~15.7k tokens for a real AFS
    )

    text_response = response.choices[0].message.content.strip()
    finish_reason = response.choices[0].finish_reason

    parsed = _extract_json_object(text_response)
    if not parsed:
        truncation_note = (
            "\n\nThe response was cut off (hit the max_tokens limit) before the JSON "
            "finished — the field list, evidence quotes, or sanity checks are likely "
            "too long for the current limit. Consider raising max_tokens further."
            if finish_reason == "length" else ""
        )
        raise ValueError(
            f"LLM did not return valid JSON (finish_reason={finish_reason}).{truncation_note}\n"
            f"Response excerpt:\n{text_response[:600]}"
        )

    if _is_new_prompt_format(parsed):
        return _adapt_new_prompt_format(parsed)

    # Legacy extraction contract format — validate directly
    valid, err = validate_extraction(parsed)
    if not valid:
        raise ValueError(f"LLM returned invalid extraction contract: {err}")
    return parsed


def verify_afs_against_sheet(
    afs_bytes: bytes,
    afs_filename: str,
    sheet_id: str,
    tab: str,
    use_fixture: bool = False,
    afs_text: str | None = None,
) -> dict:
    """
    Full pipeline: extract fields from AFS -> fetch Google Sheet row -> compare.

    Flow:
      1. Extract AFS text.
      2. Fixture path: load fixture → fetch sheet row by fixture unit no.
         Live path:  regex-extract unit no → fetch sheet row → call LLM with both.
      3. Validate extraction contract.
      4. Python comparator (authoritative verdict — LLM comparison result is ignored).

    Returns a result dict ready for UI display and database storage.
    """
    from sheets import get_worksheet, find_unit_row

    prompt_configured = not _sheet_prompt_is_placeholder()
    if afs_text is None:
        afs_text, _ = _extract_afs_text(afs_bytes, afs_filename)

    if use_fixture or not prompt_configured:
        # ── Fixture path ────────────────────────────────────────────────────
        with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
            extraction = json.load(f)
        used_fixture = True

        unit_distinct = extraction["unit_number"].get("distinct_values", [])
        if not unit_distinct:
            raise ValueError("Fixture has no unit_number.distinct_values.")
        unit_no = unit_distinct[0]

        ws = get_worksheet(sheet_id, tab)
        sheet_row = find_unit_row(ws, unit_no)

    else:
        # ── Live LLM path ───────────────────────────────────────────────────
        unit_no_hint = _quick_extract_unit_no(afs_text)
        if not unit_no_hint:
            raise ValueError(
                "Could not extract a unit number from the AFS text via regex. "
                "The document may not be in the expected format."
            )

        ws = get_worksheet(sheet_id, tab)
        sheet_row = find_unit_row(ws, unit_no_hint)

        extraction = _call_sheet_extraction_llm(afs_text, sheet_row)
        used_fixture = False

    # Validate extraction contract (catches malformed LLM output early)
    valid, err = validate_extraction(extraction)
    if not valid:
        raise ValueError(f"Extraction contract is invalid: {err}")

    afs_meta = extraction.get("afs_meta", {})

    # Python comparator is the authoritative verdict
    comparison = run_comparison(extraction, sheet_row)

    return {
        "verdict": comparison.verdict,
        "fields": comparison.fields,
        "warnings": comparison.warnings,
        "schema_caveats": comparison.schema_caveats,
        "extraction": extraction,
        "sheet_row": sheet_row,
        "afs_meta": afs_meta,
        "used_fixture": used_fixture,
        "prompt_configured": prompt_configured,
        # Informational only — reported by the LLM, not re-verified by Python.
        "derived_check_fields": extraction.get("llm_derived_check_fields", []),
        "info_only_fields": extraction.get("llm_info_only_fields", []),
        "out_of_scope_sheet_columns": extraction.get("llm_out_of_scope_sheet_columns", []),
        "low_confidence_or_unverifiable": extraction.get("llm_low_confidence_or_unverifiable", []),
        "internal_sanity_checks": extraction.get("llm_internal_sanity_checks", {}),
    }


# ── Unified verification (KYC + Sheet in one go) ───────────────────────────────

def verify_unified(
    afs_bytes: bytes,
    afs_mime: str,
    aadhaar_list: list,
    pan_list: list,
    sheet_id: str,
    tab: str,
    afs_filename: str = "afs_document.pdf",
    use_fixture: bool = False,
) -> dict:
    """
    Runs both verifications from a single AFS upload, sharing one text extraction:

      1. KYC cross-verification (AFS + Aadhaar/PAN images, LLM-authoritative).
      2. AFS ↔ Google Sheet field audit (text-only, Python-authoritative).

    The two checks remain independent LLM calls — only the AFS text extraction
    is shared — so accuracy is identical to running each tab separately.

    Returns:
      {
        "kyc":   {"report_text": str, "json_data": dict} | {"error": str},
        "sheet": <verify_afs_against_sheet result dict>   | {"error": str},
      }

    Each check is isolated: a failure in one is captured as {"error": ...} and
    does not prevent the other from running or being returned.
    """
    # Extract the AFS text exactly once and feed it to both checks.
    afs_text, afs_truncated = _extract_afs_text(afs_bytes, afs_filename)

    result: dict = {}

    try:
        kyc_report, kyc_json = verify_documents(
            afs_bytes=afs_bytes,
            afs_mime=afs_mime,
            aadhaar_list=aadhaar_list,
            pan_list=pan_list,
            afs_filename=afs_filename,
            afs_text=afs_text,
            afs_truncated=afs_truncated,
        )
        result["kyc"] = {"report_text": kyc_report, "json_data": kyc_json}
    except Exception as e:
        result["kyc"] = {"error": str(e)}

    try:
        sheet_result = verify_afs_against_sheet(
            afs_bytes=afs_bytes,
            afs_filename=afs_filename,
            sheet_id=sheet_id,
            tab=tab,
            use_fixture=use_fixture,
            afs_text=afs_text,
        )
        result["sheet"] = sheet_result
    except Exception as e:
        result["sheet"] = {"error": str(e)}

    return result
