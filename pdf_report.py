import io
import re
import datetime
from fpdf import FPDF

# ── Brand colour palette ────────────────────────────────────────────────────
NAVY        = (15,  40,  80)
BLUE        = (30,  90, 180)
LIGHT_BLUE  = (70, 130, 210)
LIGHT_BG    = (245, 248, 255)
HEADER_BG   = (20,  50,  95)
WHITE       = (255, 255, 255)
GREY_TXT    = (100, 110, 130)
DARK_TXT    = (30,  35,  45)
MATCH_BG    = (220, 242, 230)
MATCH_FG    = (22,  110,  55)
MISMATCH_BG = (253, 231, 231)
MISMATCH_FG = (185,  28,  28)
PARTIAL_BG  = (255, 247, 218)
PARTIAL_FG  = (146,  95,   0)
RULE_CLR    = (200, 210, 230)
SECTION_BG  = (235, 242, 255)


# ── Unicode → Latin-1 normaliser ────────────────────────────────────────────
_REPLACEMENTS = {
    "—": "-",
    "–": "-",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "•": "*",
    "…": "...",
    "™": "(TM)",
    "®": "(R)",
    "✅": "[MATCH]",
    "❌": "[MISMATCH]",
    "⚠️": "[WARNING]",
    "⚠": "[WARNING]",
    "\U0001f4cb": "", "\U0001f4e7": "", "\U0001f4e5": "",
    "\U0001f4be": "", "\U0001f50d": "", "\U0001f916": "",
    "\U0001f195": "", "\U0001f4da": "",
}


def _clean(text: str) -> str:
    for char, rep in _REPLACEMENTS.items():
        text = text.replace(char, rep)
    return text.encode("latin-1", "replace").decode("latin-1")


# ── Status badge ─────────────────────────────────────────────────────────────
def _status_badge(pdf: FPDF, label: str, w: float, h: float):
    lbl = label.strip()
    if "✅" in lbl or "[MATCH]" in lbl.upper():
        bg, fg, text = MATCH_BG, MATCH_FG, "MATCH"
    elif "❌" in lbl or "[MISMATCH]" in lbl.upper():
        bg, fg, text = MISMATCH_BG, MISMATCH_FG, "MISMATCH"
    elif "⚠" in lbl or "[WARNING]" in lbl.upper() or "PARTIAL" in lbl.upper():
        bg, fg, text = PARTIAL_BG, PARTIAL_FG, "PARTIAL"
    else:
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*DARK_TXT)
        pdf.cell(w, h, _clean(lbl), border=0, align="C", new_x="RIGHT", new_y="TOP")
        return

    x, y = pdf.get_x(), pdf.get_y()
    pad_x = 2.5
    # Filled rect with colored border
    pdf.set_fill_color(*bg)
    pdf.set_draw_color(*fg)
    pdf.set_line_width(0.4)
    pdf.rect(x + pad_x, y + 1.5, w - 2 * pad_x, h - 3, "FD")
    pdf.set_line_width(0.2)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(*fg)
    pdf.cell(w, h, text, border=0, align="C", new_x="RIGHT", new_y="TOP")
    pdf.set_draw_color(*RULE_CLR)


# ── Table parser ─────────────────────────────────────────────────────────────
def _parse_table(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        if re.match(r"^\|[\s\-:]+\|", line):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if cells:
            rows.append(cells)
    return rows


# ── Cover page ───────────────────────────────────────────────────────────────
def _draw_cover(pdf: FPDF, buyer_name: str, json_data: dict):
    pdf.add_page()

    # Full-width navy header banner
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 70, "F")

    # Top thin accent stripe (blue)
    pdf.set_fill_color(*BLUE)
    pdf.rect(0, 0, 210, 2.5, "F")

    # Bottom accent stripes on header
    pdf.set_fill_color(*LIGHT_BLUE)
    pdf.rect(0, 66.5, 210, 2, "F")
    pdf.set_fill_color(*BLUE)
    pdf.rect(0, 68.5, 210, 1.5, "F")

    # "KYC" logotype
    pdf.set_xy(0, 10)
    pdf.set_font("Helvetica", "B", 46)
    pdf.set_text_color(*WHITE)
    pdf.cell(210, 26, "KYC", align="C", new_x="LMARGIN", new_y="NEXT")

    # Subtitle
    pdf.set_xy(0, 42)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(180, 200, 235)
    pdf.cell(210, 7, "VERIFICATION REPORT", align="C", new_x="LMARGIN", new_y="NEXT")

    # Thin divider line under subtitle
    pdf.set_draw_color(*LIGHT_BLUE)
    pdf.set_line_width(0.5)
    pdf.line(75, 52, 135, 52)
    pdf.set_line_width(0.2)

    # "CONFIDENTIAL" label — right-aligned inside header
    pdf.set_xy(10, 58)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(160, 185, 225)
    pdf.cell(190, 6, "CONFIDENTIAL DOCUMENT", align="R")

    # ── Meta info card ──────────────────────────────────────────────────────
    card_x, card_y, card_w, card_h = 14, 80, 182, 105
    # Card shadow simulation (offset dark rect)
    pdf.set_fill_color(210, 215, 225)
    pdf.rect(card_x + 1.5, card_y + 1.5, card_w, card_h, "F")
    # Card background
    pdf.set_fill_color(*WHITE)
    pdf.set_draw_color(*RULE_CLR)
    pdf.set_line_width(0.3)
    pdf.rect(card_x, card_y, card_w, card_h, "FD")
    # Left blue accent bar
    pdf.set_fill_color(*BLUE)
    pdf.rect(card_x, card_y, 4.5, card_h, "F")

    # Card title
    pdf.set_xy(card_x + 8, card_y + 6)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(*GREY_TXT)
    pdf.cell(card_w - 12, 6, "REPORT DETAILS")

    # Separator under card title
    pdf.set_draw_color(*RULE_CLR)
    pdf.set_line_width(0.3)
    pdf.line(card_x + 8, card_y + 14, card_x + card_w - 4, card_y + 14)
    pdf.set_line_width(0.2)

    buyer_display   = buyer_name or json_data.get("buyer_name", "Unknown")
    project_display = json_data.get("project_name", "—")
    unit_display    = json_data.get("unit_number", "—")
    afs_date        = json_data.get("afs_date", "—")
    gen_date        = datetime.datetime.now().strftime("%d %B %Y, %I:%M %p")
    status          = json_data.get("status", "—")
    report_id       = datetime.datetime.now().strftime("KYC-%Y%m%d-%H%M")

    pdf.set_y(card_y + 18)

    def meta_row(label: str, value: str):
        pdf.set_x(card_x + 8)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*GREY_TXT)
        pdf.cell(46, 8, label.upper())
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*DARK_TXT)
        pdf.cell(card_w - 58, 8, _clean(value), new_x="LMARGIN", new_y="NEXT")

    meta_row("Buyer(s)", buyer_display)
    meta_row("Project", project_display)
    meta_row("Unit / Flat", unit_display)
    meta_row("AFS Date", afs_date)
    meta_row("Verified On", gen_date)
    meta_row("Report ID", report_id)

    # ── Status badge (bottom of card) ───────────────────────────────────────
    if status == "MATCH":
        bg, fg, label_txt = (210, 240, 220), MATCH_FG, "ALL FIELDS MATCH"
    else:
        bg, fg, label_txt = (252, 226, 226), MISMATCH_FG, "MISMATCH DETECTED"

    badge_y = card_y + card_h - 22
    pdf.set_fill_color(*bg)
    pdf.set_draw_color(*fg)
    pdf.set_line_width(0.6)
    pdf.rect(card_x + 8, badge_y, card_w - 16, 17, "FD")
    pdf.set_line_width(0.2)
    pdf.set_xy(card_x + 8, badge_y)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*fg)
    pdf.cell(card_w - 16, 17, label_txt, align="C", new_x="LMARGIN", new_y="NEXT")

    # ── Footer disclaimer ────────────────────────────────────────────────────
    disc_y = card_y + card_h + 8
    pdf.set_xy(14, disc_y)
    pdf.set_draw_color(*RULE_CLR)
    pdf.set_line_width(0.3)
    pdf.line(14, disc_y, 196, disc_y)
    pdf.set_line_width(0.2)
    pdf.set_xy(14, disc_y + 4)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*GREY_TXT)
    pdf.multi_cell(
        182, 5,
        "This report is auto-generated by the KYC Verification Agent and is intended solely for "
        "internal use by authorised personnel. Do not distribute externally.",
        align="C",
    )

    # Page number
    pdf.set_y(-14)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(*GREY_TXT)
    pdf.cell(0, 10, f"Page 1 / {{nb}}   |   KYC Verification Agent   |   {report_id}", align="C")


# ── Main PDF class ────────────────────────────────────────────────────────────
class KYCReportPDF(FPDF):
    def __init__(self, json_data: dict = None):
        super().__init__()
        self._json_data = json_data or {}
        self.set_auto_page_break(auto=True, margin=22)

    def header(self):
        if self.page_no() == 1:
            return
        # Navy strip
        self.set_fill_color(*NAVY)
        self.rect(0, 0, 210, 12, "F")
        # Blue accent line below strip
        self.set_fill_color(*BLUE)
        self.rect(0, 12, 210, 1.5, "F")

        self.set_xy(0, 2)
        self.set_font("Helvetica", "B", 7.5)
        self.set_text_color(*WHITE)
        self.cell(105, 8, "  KYC VERIFICATION REPORT")
        self.set_font("Helvetica", "", 7.5)
        buyer   = _clean(self._json_data.get("buyer_name", ""))
        project = _clean(self._json_data.get("project_name", ""))
        right_txt = f"{project}  |  {buyer}  " if project else f"{buyer}  "
        self.cell(105, 8, right_txt, align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*RULE_CLR)
        self.ln(5)

    def footer(self):
        # Tinted footer background
        self.set_y(-14)
        self.set_fill_color(*LIGHT_BG)
        self.rect(0, self.get_y(), 210, 16, "F")
        # Blue accent line at top of footer
        self.set_fill_color(*BLUE)
        self.rect(0, self.get_y(), 210, 0.8, "F")
        self.ln(2)
        self.set_font("Helvetica", "I", 7.5)
        self.set_text_color(*GREY_TXT)
        gen = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
        self.cell(
            0, 6,
            f"Page {self.page_no()}/{{nb}}   |   Generated {gen}   |   CONFIDENTIAL",
            align="C",
        )


# ── Table renderer ───────────────────────────────────────────────────────────
def _render_table(pdf: FPDF, rows: list[list[str]]):
    if not rows:
        return

    num_cols = len(rows[0])
    page_w = 188

    if num_cols == 6:
        col_widths = [18, 28, 38, 35, 30, 39]
    elif num_cols <= 3:
        col_widths = [page_w / num_cols] * num_cols
    else:
        col_widths = [page_w / num_cols] * num_cols

    total = sum(col_widths)
    col_widths = [w * page_w / total for w in col_widths]

    line_h = 5.5
    is_header = True

    for row_idx, row in enumerate(rows):
        while len(row) < num_cols:
            row.append("")

        cell_texts = []
        for cell in row:
            c = re.sub(r"\*\*(.*?)\*\*", r"\1", cell)
            cell_texts.append(c.strip())

        # Estimate row height
        max_lines = 1
        for j, ct in enumerate(cell_texts):
            cw = col_widths[j]
            avg_char_w = pdf.get_string_width("A") or 1.8
            chars_per_line = max(1, int(cw / avg_char_w))
            needed = max(1, -(-len(_clean(ct)) // chars_per_line))
            max_lines = max(max_lines, needed)
        row_h = line_h * max_lines

        if pdf.get_y() + row_h > 272:
            pdf.add_page()

        x0 = pdf.get_x()
        y0 = pdf.get_y()

        if is_header:
            # Header background
            pdf.set_fill_color(*HEADER_BG)
            pdf.rect(x0, y0, sum(col_widths), row_h, "F")

            pdf.set_text_color(*WHITE)
            pdf.set_font("Helvetica", "B", 7)
            for j, ct in enumerate(cell_texts):
                pdf.set_xy(x0 + sum(col_widths[:j]), y0)
                pdf.multi_cell(
                    col_widths[j], line_h, _clean(ct),
                    border=0, fill=False,
                    new_x="RIGHT", new_y="TOP",
                    max_line_height=line_h, align="C",
                )
                # Column separator (lighter on dark bg)
                if j < num_cols - 1:
                    sep_x = x0 + sum(col_widths[:j + 1])
                    pdf.set_draw_color(60, 90, 140)
                    pdf.set_line_width(0.3)
                    pdf.line(sep_x, y0, sep_x, y0 + row_h)
                    pdf.set_line_width(0.2)

            pdf.set_y(y0 + row_h)
            is_header = False
        else:
            fill_bg = LIGHT_BG if row_idx % 2 == 0 else WHITE
            pdf.set_fill_color(*fill_bg)
            pdf.set_draw_color(*RULE_CLR)
            pdf.rect(x0, y0, sum(col_widths), row_h, "FD")

            for j, ct in enumerate(cell_texts):
                pdf.set_xy(x0 + sum(col_widths[:j]), y0)

                if j == 0 and num_cols == 6:
                    _status_badge(pdf, ct, col_widths[j], row_h)
                else:
                    pdf.set_font("Helvetica", "", 7)
                    pdf.set_text_color(*DARK_TXT)
                    pdf.multi_cell(
                        col_widths[j], line_h, _clean(ct),
                        border=0, fill=False,
                        new_x="RIGHT", new_y="TOP",
                        max_line_height=line_h,
                    )

                # Column separator line
                if j < num_cols - 1:
                    sep_x = x0 + sum(col_widths[:j + 1])
                    pdf.set_draw_color(*RULE_CLR)
                    pdf.set_line_width(0.2)
                    pdf.line(sep_x, y0, sep_x, y0 + row_h)

            pdf.set_y(y0 + row_h)


# ── Main generator ───────────────────────────────────────────────────────────
def generate_pdf_report(
    report_markdown: str,
    buyer_name: str = "Unknown",
    json_data: dict = None,
) -> bytes:
    if json_data is None:
        json_data = {}

    pdf = KYCReportPDF(json_data=json_data)
    pdf.alias_nb_pages()

    _draw_cover(pdf, buyer_name, json_data)

    pdf.add_page()
    pdf.set_left_margin(12)
    pdf.set_right_margin(12)

    lines = report_markdown.split("\n")
    i = 0

    while i < len(lines):
        line     = lines[i]
        stripped = line.strip()

        if stripped == "# KYC VERIFICATION REPORT":
            i += 1
            continue

        # ── H1 ──
        if stripped.startswith("# ") and not stripped.startswith("##"):
            pdf.ln(3)
            # Light-blue tinted band spanning full width
            y_now = pdf.get_y()
            pdf.set_fill_color(*SECTION_BG)
            pdf.rect(10, y_now, 190, 12, "F")
            pdf.set_fill_color(*BLUE)
            pdf.rect(10, y_now, 5, 12, "F")
            pdf.set_xy(18, y_now)
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_text_color(*NAVY)
            pdf.cell(182, 12, _clean(stripped[2:]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)
            i += 1
            continue

        # ── H2 ──
        if stripped.startswith("## "):
            pdf.ln(4)
            y_now = pdf.get_y()
            # Full-width tinted background
            pdf.set_fill_color(*SECTION_BG)
            pdf.rect(10, y_now, 190, 10, "F")
            # Thick left accent bar
            pdf.set_fill_color(*BLUE)
            pdf.rect(10, y_now, 4, 10, "F")
            pdf.set_xy(17, y_now)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*NAVY)
            pdf.cell(183, 10, _clean(stripped[3:]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)
            i += 1
            continue

        # ── H3 ──
        if stripped.startswith("### "):
            pdf.ln(3)
            y_now = pdf.get_y()
            pdf.set_fill_color(*LIGHT_BLUE)
            pdf.rect(13, y_now + 1, 2.5, 7, "F")
            pdf.set_xy(19, y_now)
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(*DARK_TXT)
            pdf.cell(0, 9, _clean(stripped[4:]), new_x="LMARGIN", new_y="NEXT")
            # Dotted underline
            pdf.set_draw_color(*RULE_CLR)
            pdf.set_line_width(0.3)
            pdf.line(13, pdf.get_y(), 197, pdf.get_y())
            pdf.set_line_width(0.2)
            pdf.ln(3)
            i += 1
            continue

        # ── Horizontal rule ──
        if stripped in ("---", "***", "___"):
            pdf.ln(2)
            pdf.set_draw_color(*RULE_CLR)
            pdf.line(12, pdf.get_y(), 198, pdf.get_y())
            pdf.ln(4)
            i += 1
            continue

        # ── Table block ──
        if stripped.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            rows = _parse_table(table_lines)
            if rows:
                pdf.ln(2)
                _render_table(pdf, rows)
                pdf.ln(4)
            continue

        # ── Bullet points ──
        if stripped.startswith("* ") or stripped.startswith("- "):
            content = stripped[2:]

            if content.startswith("**") and "**" in content[2:]:
                bold_end  = content.index("**", 2)
                bold_text = content[2:bold_end]
                rest      = content[bold_end + 2:]

                if "OVERALL STATUS" in bold_text.upper():
                    status_text = _clean(rest).strip()
                    if "MATCH" in status_text.upper() and "MISMATCH" not in status_text.upper():
                        box_bg, box_fg = MATCH_BG, MATCH_FG
                        icon = "[MATCH]"
                    elif "MISMATCH" in status_text.upper() or "NOT" in status_text.upper():
                        box_bg, box_fg = MISMATCH_BG, MISMATCH_FG
                        icon = "[MISMATCH]"
                    else:
                        box_bg, box_fg = PARTIAL_BG, PARTIAL_FG
                        icon = "[REVIEW]"

                    clean_rest = re.sub(r"[✅❌⚠️⚠]", "", rest).strip()
                    clean_rest = _clean(clean_rest)

                    bx, by = pdf.get_x(), pdf.get_y()
                    pdf.set_fill_color(*box_bg)
                    pdf.set_draw_color(*box_fg)
                    pdf.set_line_width(0.5)
                    pdf.rect(bx, by, 186, 11, "FD")
                    pdf.set_line_width(0.2)
                    # Left accent bar inside the status box
                    pdf.set_fill_color(*box_fg)
                    pdf.rect(bx, by, 4, 11, "F")
                    pdf.set_xy(bx + 6, by)
                    pdf.set_font("Helvetica", "B", 9)
                    pdf.set_text_color(*box_fg)
                    pdf.cell(44, 11, bold_text)
                    pdf.set_font("Helvetica", "", 9)
                    pdf.cell(136, 11, f"{icon} {clean_rest}", new_x="LMARGIN", new_y="NEXT")
                    pdf.ln(2)
                    pdf.set_draw_color(*RULE_CLR)
                    i += 1
                    continue

                # Other bold-label bullets
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_text_color(*DARK_TXT)
                pdf.set_x(16)
                pdf.cell(3, 6, "-")
                pdf.cell(pdf.get_string_width(_clean(bold_text)) + 2, 6, _clean(bold_text))
                pdf.set_font("Helvetica", "", 9)
                clean_rest = re.sub(r"[✅❌⚠️⚠]", "", rest).strip()
                pdf.multi_cell(0, 6, _clean(clean_rest), new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.set_font("Helvetica", "", 9)
                pdf.set_text_color(*DARK_TXT)
                pdf.set_x(16)
                pdf.cell(3, 6, "-")
                pdf.multi_cell(0, 6, _clean(content), new_x="LMARGIN", new_y="NEXT")

            i += 1
            continue

        # ── Code / pre-formatted block ──
        if stripped.startswith("```"):
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            if code_lines:
                pdf.ln(2)
                pdf.set_fill_color(238, 242, 252)
                pdf.set_draw_color(*RULE_CLR)
                box_h = len(code_lines) * 5 + 4
                bx, by = pdf.get_x(), pdf.get_y()
                # Left accent bar for code blocks
                pdf.set_fill_color(238, 242, 252)
                pdf.rect(bx, by, 186, box_h, "FD")
                pdf.set_fill_color(*LIGHT_BLUE)
                pdf.rect(bx, by, 3, box_h, "F")
                pdf.set_xy(bx + 6, by + 2)
                pdf.set_font("Courier", "", 7.5)
                pdf.set_text_color(*DARK_TXT)
                for cl in code_lines:
                    pdf.set_x(bx + 6)
                    pdf.cell(180, 5, _clean(cl), new_x="LMARGIN", new_y="NEXT")
                pdf.ln(3)
            continue

        # ── Plain paragraph ──
        if stripped:
            pdf.set_font("Helvetica", "", 9.5)
            pdf.set_text_color(*DARK_TXT)
            clean = re.sub(r"\*\*(.*?)\*\*", r"\1", stripped)
            clean = re.sub(r"[✅]", "[MATCH]", clean)
            clean = re.sub(r"[❌]", "[MISMATCH]", clean)
            clean = re.sub(r"[⚠️⚠]", "[WARNING]", clean)
            pdf.multi_cell(0, 6, _clean(clean), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)

        i += 1

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return buf.getvalue()


# ── Sheet audit PDF ───────────────────────────────────────────────────────────

def generate_sheet_audit_pdf(result: dict) -> bytes:
    """
    Generates a branded PDF for an AFS ↔ Sheet audit result.

    'result' is the dict returned by agent.verify_afs_against_sheet().
    """
    afs_meta = result.get("afs_meta", {})
    verdict = result.get("verdict", "FAIL")
    fields = result.get("fields", [])
    warnings = result.get("warnings", [])
    schema_caveats = result.get("schema_caveats", [])
    extraction = result.get("extraction", {})

    unit_no = (
        extraction.get("unit_number", {}).get("distinct_values", [""])[0]
        if extraction else ""
    )

    # Build a json_data shape compatible with _draw_cover
    cover_data = {
        "buyer_name": afs_meta.get("buyer_name", ""),
        "project_name": afs_meta.get("project_name", ""),
        "unit_number": unit_no,
        "afs_date": afs_meta.get("afs_date", ""),
        "status": "MATCH" if verdict == "PASS" else "MISMATCH",
    }

    pdf = KYCReportPDF(json_data=cover_data)
    pdf.alias_nb_pages()

    _draw_cover(pdf, afs_meta.get("buyer_name", ""), cover_data)

    # ── Results page ──
    pdf.add_page()
    pdf.set_left_margin(12)
    pdf.set_right_margin(12)

    # Section heading
    y_now = pdf.get_y()
    pdf.set_fill_color(*SECTION_BG)
    pdf.rect(10, y_now, 190, 10, "F")
    pdf.set_fill_color(*BLUE)
    pdf.rect(10, y_now, 4, 10, "F")
    pdf.set_xy(17, y_now)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*NAVY)
    pdf.cell(183, 10, _clean("AFS vs Google Sheet - Field Verification"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Verdict banner
    v_bg = MATCH_BG if verdict == "PASS" else MISMATCH_BG
    v_fg = MATCH_FG if verdict == "PASS" else MISMATCH_FG
    v_txt = "PASS - ALL FIELDS VERIFIED" if verdict == "PASS" else "FAIL - MISMATCH DETECTED"
    bx, by = pdf.get_x(), pdf.get_y()
    pdf.set_fill_color(*v_bg)
    pdf.set_draw_color(*v_fg)
    pdf.set_line_width(0.6)
    pdf.rect(bx, by, 186, 12, "FD")
    pdf.set_fill_color(*v_fg)
    pdf.rect(bx, by, 4, 12, "F")
    pdf.set_line_width(0.2)
    pdf.set_xy(bx + 8, by)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*v_fg)
    pdf.cell(178, 12, _clean(v_txt), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    pdf.set_draw_color(*RULE_CLR)

    # Field table
    # Columns: Status | Field | AFS Raw | Sheet Raw | AFS Norm | Sheet Norm | Detail
    col_w = [20, 28, 38, 28, 22, 22, 50]
    headers = ["Status", "Field", "AFS Raw Value(s)", "Sheet Raw", "AFS Norm.", "Sheet Norm.", "Notes"]
    line_h = 5.5
    page_w = sum(col_w)

    # Header row
    x0, y0 = pdf.get_x(), pdf.get_y()
    pdf.set_fill_color(*HEADER_BG)
    pdf.rect(x0, y0, page_w, line_h + 2, "F")
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 6.5)
    for j, (hdr, cw) in enumerate(zip(headers, col_w)):
        pdf.set_xy(x0 + sum(col_w[:j]), y0)
        pdf.cell(cw, line_h + 2, hdr, border=0, align="C", new_x="RIGHT", new_y="TOP")
    pdf.set_y(y0 + line_h + 2)
    pdf.set_draw_color(*RULE_CLR)

    # Data rows
    for row_idx, f in enumerate(fields):
        # Coerce to dict (works for both FieldResult dataclass and plain dict)
        if not isinstance(f, dict):
            fd = {
                "field_name": f.field_name, "status": f.status,
                "afs_occurrences": f.afs_occurrences,
                "afs_distinct_values": f.afs_distinct_values,
                "sheet_raw": f.sheet_raw, "afs_normalized": f.afs_normalized,
                "sheet_normalized": f.sheet_normalized, "detail": f.detail,
            }
        else:
            fd = f

        status_lbl = fd.get("status", "")
        raw_afs = "; ".join(
            f"{o.get('location','')}: {o.get('raw_text','')}"
            for o in fd.get("afs_occurrences", [])
        ) or " | ".join(str(v) for v in fd.get("afs_distinct_values", []))

        cells = [
            status_lbl,
            fd.get("field_name", ""),
            raw_afs,
            str(fd.get("sheet_raw", "")),
            str(fd.get("afs_normalized") or "—"),
            str(fd.get("sheet_normalized") or "—"),
            _clean(str(fd.get("detail") or "")),
        ]

        # Estimate row height
        max_lines = 1
        for j, ct in enumerate(cells):
            avg_w = pdf.get_string_width("A") or 1.8
            chars = max(1, int(col_w[j] / avg_w))
            max_lines = max(max_lines, max(1, -(-len(_clean(ct)) // chars)))
        row_h = line_h * max_lines

        if pdf.get_y() + row_h > 272:
            pdf.add_page()

        x0, y0 = pdf.get_x(), pdf.get_y()
        fill = LIGHT_BG if row_idx % 2 == 0 else WHITE
        pdf.set_fill_color(*fill)
        pdf.set_draw_color(*RULE_CLR)
        pdf.rect(x0, y0, page_w, row_h, "FD")

        for j, (ct, cw) in enumerate(zip(cells, col_w)):
            pdf.set_xy(x0 + sum(col_w[:j]), y0)
            if j == 0:
                _status_badge(pdf, ct, cw, row_h)
            else:
                pdf.set_font("Helvetica", "", 6.5)
                pdf.set_text_color(*DARK_TXT)
                pdf.multi_cell(cw, line_h, _clean(ct), border=0, fill=False,
                               new_x="RIGHT", new_y="TOP", max_line_height=line_h)
            if j < len(col_w) - 1:
                sep_x = x0 + sum(col_w[:j + 1])
                pdf.set_draw_color(*RULE_CLR)
                pdf.line(sep_x, y0, sep_x, y0 + row_h)

        pdf.set_y(y0 + row_h)

    # Warnings & caveats
    all_notes = warnings + schema_caveats
    if all_notes:
        pdf.ln(5)
        for note in all_notes:
            pdf.set_x(12)
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*GREY_TXT)
            pdf.multi_cell(186, 5, f"[NOTE] {_clean(note)}", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return buf.getvalue()
