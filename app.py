import streamlit as st
import os
import pandas as pd
from agent import verify_documents, verify_afs_against_sheet, verify_unified, _sheet_prompt_is_placeholder, extract_id_documents
from notifier import generate_match_email, generate_mismatch_email, generate_sheet_audit_email
from database import (
    init_db, save_verification, get_all_verifications_df, get_report_by_id,
    save_sheet_audit, get_all_sheet_audits_df, get_sheet_audit_by_id,
)
from pdf_report import generate_pdf_report, generate_sheet_audit_pdf
from sheets import (
    append_kyc_log, append_afs_log, update_unit_row,
    PRIMARY_APPLICANT_FIELD_MAP, CO_APPLICANT_FIELD_MAP,
)
from comparator import MATCH, MISMATCH, SCHEMA_CAVEAT, INTERNAL_DISCREPANCY, NOT_FOUND_IN_AFS, NOT_FOUND_IN_SHEET, INFO_ONLY


# ── Shared result renderers (used by both individual tabs and the unified tab) ──

def render_kyc_result(report_text, json_data, crm_email, key_prefix):
    """Render a KYC verification result: history save, CRM email, PDF, report."""
    st.subheader("KYC Verification — Action Taken")
    status = json_data.get("status", "MISMATCH")
    buyer_name = json_data.get("buyer_name", "Unknown Client")
    project_name = json_data.get("project_name", "Unknown Project")
    unit_number = json_data.get("unit_number", "Unknown Unit")
    afs_date = json_data.get("afs_date", "Unknown Date")

    save_verification(buyer_name, project_name, unit_number, status, report_text)
    st.toast("💾 KYC record saved to History!")

    try:
        append_kyc_log(buyer_name, project_name, unit_number, status, report_text)
        st.toast("📊 Logged to Google Sheet!")
    except Exception as e:
        st.warning(f"⚠️ Could not log to Google Sheet: {e}")

    if status == "MATCH":
        st.info("✅ All KYC fields match. Triggering success email to CRM.")
        success = generate_match_email(
            crm_email=crm_email,
            buyer_name=buyer_name,
            project_name=project_name,
            unit_number=unit_number,
            afs_date=afs_date,
            report_text=report_text,
        )
    else:
        st.error("❌ KYC mismatches found. Triggering action-required email to CRM.")
        success = generate_mismatch_email(
            crm_email=crm_email,
            buyer_name=buyer_name,
            project_name=project_name,
            unit_number=unit_number,
            afs_date=afs_date,
            mismatches_text=json_data.get("mismatches_text", "Please review the attached report for mismatches."),
            report_text=report_text,
        )

    if success:
        st.toast("📧 KYC email successfully sent to CRM!")
    else:
        st.warning("⚠️ Could not send KYC email. Please check your SMTP credentials in the .env file.")

    st.subheader("KYC Verification Report")
    pdf_bytes = generate_pdf_report(report_text, buyer_name, json_data=json_data)
    st.download_button(
        label="📥 Download KYC Report as PDF",
        data=pdf_bytes,
        file_name=f"KYC_Report_{buyer_name.replace(' ', '_')}.pdf",
        mime="application/pdf",
        key=f"{key_prefix}_kyc_pdf",
    )
    st.markdown(report_text)


def render_sheet_result(result, sheet_id, tab_name, afs_filename, crm_email, key_prefix):
    """Render an AFS↔Sheet audit result: verdict, field table, history, email, PDF."""
    verdict = result["verdict"]
    fields = result["fields"]
    warnings = result["warnings"]
    schema_caveats = result["schema_caveats"]
    afs_meta = result["afs_meta"]
    buyer_name = afs_meta.get("buyer_name", "Unknown")
    project_name = afs_meta.get("project_name", "Unknown")
    unit_no = (
        result["extraction"].get("unit_number", {}).get("distinct_values", ["?"])[0]
    )

    if result["used_fixture"]:
        st.info("ℹ️ Results shown using **fixture data** (Unit 313). Live extraction requires a configured prompt.")

    # Verdict banner
    if verdict == "PASS":
        st.success("✅ **PASS** — All verified fields match the Google Sheet.")
    else:
        st.error("❌ **FAIL** — One or more fields do not match the Google Sheet.")

    # Warnings and caveats
    for w in warnings:
        st.warning(f"⚠️ {w}")
    for c in schema_caveats:
        st.info(f"ℹ️ Schema caveat: {c}")

    # ── Field-by-field table ────────────────────────────────────────
    st.subheader("Field-by-Field Results")

    STATUS_EMOJI = {
        MATCH: "✅ MATCH",
        MISMATCH: "❌ MISMATCH",
        SCHEMA_CAVEAT: "⚠️ SCHEMA CAVEAT",
        INTERNAL_DISCREPANCY: "🔴 INTERNAL DISCREPANCY",
        NOT_FOUND_IN_AFS: "❓ NOT IN AFS",
        NOT_FOUND_IN_SHEET: "❓ NOT IN SHEET",
        INFO_ONLY: "ℹ️ INFO ONLY",
    }

    rows = []
    for f in fields:
        afs_raw = "; ".join(
            f"{o.get('location','')}: {o.get('raw_text','')}"
            for o in f.afs_occurrences
        ) or " | ".join(f.afs_distinct_values)
        rows.append({
            "Status": STATUS_EMOJI.get(f.status, f.status),
            "Field": f.field_name,
            "AFS Raw Occurrences": afs_raw,
            "Sheet Raw": str(f.sheet_raw),
            "AFS Normalized": str(f.afs_normalized) if f.afs_normalized else "—",
            "Sheet Normalized": str(f.sheet_normalized) if f.sheet_normalized else "—",
            "Detail / Notes": f.detail or "",
        })

    df_fields = pd.DataFrame(rows)

    def _colour_status(val):
        if "MATCH" in val and "MISMATCH" not in val:
            return "background-color: #d1fae5; color: #065f46;"
        if "MISMATCH" in val or "DISCREPANCY" in val:
            return "background-color: #fee2e2; color: #991b1b;"
        if "CAVEAT" in val or "NOT IN" in val:
            return "background-color: #fef3c7; color: #92400e;"
        if "INFO" in val:
            return "background-color: #e0e7ff; color: #3730a3;"
        return ""

    styled = df_fields.style.map(_colour_status, subset=["Status"])
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── Low-confidence / unverifiable flags (reported by the LLM) ──
    low_confidence = result.get("low_confidence_or_unverifiable", [])
    if low_confidence:
        st.warning(
            "⚠️ **Needs human review** — the extraction flagged these as "
            "low-confidence or unverifiable:\n\n"
            + "\n".join(f"- {item}" for item in low_confidence)
        )

    # ── Derived-check fields (rate, milestones — informational, not verdict-affecting) ──
    derived_fields = result.get("derived_check_fields", [])
    if derived_fields:
        st.subheader("Derived Checks (model-computed — verify independently)")
        derived_rows = [{
            "Field": d.get("field", ""),
            "Operation": d.get("operation", ""),
            "Operands": str(d.get("operands", "")),
            "Computed Result": str(d.get("model_computed_result", "")),
            "Sheet Raw": str(d.get("sheet_raw", "")),
            "Status": d.get("status", ""),
            "Note": d.get("note", ""),
        } for d in derived_fields]
        st.dataframe(pd.DataFrame(derived_rows), use_container_width=True, hide_index=True)

    # ── Info-only fields (not in AFS-vs-Sheet scope, reported for context) ──
    info_fields = result.get("info_only_fields", [])
    if info_fields:
        st.subheader("Info-Only Fields (no verdict impact)")
        info_rows = []
        for inf in info_fields:
            occ_text = "; ".join(
                f"{o.get('anchor','')}: {o.get('raw', o.get('evidence',''))}"
                for o in inf.get("afs_occurrences", [])
            )
            info_rows.append({
                "Field": inf.get("field", ""),
                "AFS Occurrences": occ_text,
                "AFS Internal Status": inf.get("afs_internal_status", ""),
                "Note": inf.get("note", ""),
            })
        st.dataframe(pd.DataFrame(info_rows), use_container_width=True, hide_index=True)

    # ── Internal sanity checks (figure-vs-words, sq.m→sq.ft, sums — model-computed) ──
    sanity_checks = result.get("internal_sanity_checks", {})
    if sanity_checks:
        with st.expander("🧮 Internal Sanity Checks (model-computed, verify independently)"):
            for key, val in sanity_checks.items():
                st.markdown(f"**{key.replace('_', ' ').title()}**: {val}")

    # ── Sheet columns the prompt explicitly treats as out of scope ──
    out_of_scope = result.get("out_of_scope_sheet_columns", [])
    if out_of_scope:
        with st.expander("Out-of-scope Sheet columns"):
            st.caption(", ".join(out_of_scope))

    # ── Save to history ─────────────────────────────────────────────
    save_sheet_audit(
        unit_no=unit_no,
        buyer_name=buyer_name,
        project_name=project_name,
        sheet_id=sheet_id,
        tab_name=tab_name,
        verdict=verdict,
        fields=fields,
        afs_filename=afs_filename,
    )
    st.toast("💾 Audit record saved to History!")

    try:
        append_afs_log(
            unit_no=unit_no,
            buyer_name=buyer_name,
            project_name=project_name,
            sheet_id=sheet_id,
            tab_name=tab_name,
            verdict=verdict,
            fields=fields,
            afs_filename=afs_filename,
        )
        st.toast("📊 Logged to Google Sheet!")
    except Exception as e:
        st.warning(f"⚠️ Could not log to Google Sheet: {e}")

    # ── Email ────────────────────────────────────────────────────────
    email_ok = generate_sheet_audit_email(
        crm_email=crm_email,
        buyer_name=buyer_name,
        unit_no=unit_no,
        project_name=project_name,
        verdict=verdict,
        fields=fields,
        warnings=warnings,
    )
    if email_ok:
        st.toast("📧 Audit email sent to CRM!")
    else:
        st.warning("⚠️ Could not send audit email. Check SMTP credentials in .env.")

    # ── PDF download ─────────────────────────────────────────────────
    pdf_bytes = generate_sheet_audit_pdf(result)
    st.download_button(
        label="📥 Download Audit Report as PDF",
        data=pdf_bytes,
        file_name=f"SheetAudit_{unit_no}_{buyer_name.replace(' ', '_')}.pdf",
        mime="application/pdf",
        key=f"{key_prefix}_sheet_pdf",
    )


# Initialize database once
@st.cache_resource
def run_db_init():
    init_db()

run_db_init()

st.set_page_config(page_title="KYC Verification Agent", page_icon="📋", layout="wide")

st.title("📋 Real Estate KYC Verification Agent")
st.markdown("Upload the client's Agreement for Sale (AFS) and their KYC documents. The agent will cross-verify every field and automatically email the CRM team.")

with st.sidebar:
    st.header("Settings")
    crm_email = st.text_input("CRM Officer Email", value=os.environ.get("SMTP_EMAIL", "crm@example.com"))

    st.markdown("---")
    st.markdown("**Instructions:**")
    st.markdown("1. Upload the AFS (PDF format).")
    st.markdown("2. Upload Aadhaar Card(s) (Image or PDF). Upload multiple for co-applicants.")
    st.markdown("3. Upload PAN Card(s) (Image or PDF). Upload multiple for co-applicants.")
    st.markdown("4. Click **Run Verification**.")
    st.markdown("---")
    st.caption("⚠️ Max file size: 15 MB per document.")

tab0, tab1, tab2, tab3, tab4 = st.tabs([
    "🚀 Unified Verification",
    "🆕 KYC Verification",
    "🔢 AFS ↔ Sheet Audit",
    "📚 Verification History",
    "🪪 Update KYC → Sheet",
])

with tab0:
    st.header("Unified Verification — KYC + Sheet Audit in one go")
    st.markdown(
        "Upload the AFS **once**. The agent runs both checks from a single extraction: "
        "(1) **KYC cross-verification** against Aadhaar/PAN, and (2) the **AFS ↔ Google Sheet** "
        "field audit. The two checks stay independent under the hood, so accuracy is identical "
        "to running each tab separately."
    )

    if _sheet_prompt_is_placeholder():
        st.warning(
            "⚙️ **Sheet extraction prompt not configured.** `afs_sheet_system_prompt.md` is still a "
            "placeholder, so the Sheet audit will use the **built-in fixture** (Unit 313). The KYC "
            "check runs normally."
        )

    u_col1, u_col2, u_col3 = st.columns(3)
    with u_col1:
        u_afs_file = st.file_uploader("1. Agreement for Sale (AFS)", type=["pdf"], key="u_afs")
    with u_col2:
        u_aadhaar_files = st.file_uploader(
            "2. Aadhaar Card(s)", type=["pdf", "png", "jpg", "jpeg"],
            accept_multiple_files=True, key="u_aadhaar",
        )
    with u_col3:
        u_pan_files = st.file_uploader(
            "3. PAN Card(s)", type=["pdf", "png", "jpg", "jpeg"],
            accept_multiple_files=True, key="u_pan",
        )

    u_col_s1, u_col_s2 = st.columns(2)
    with u_col_s1:
        u_default_sheet_id = os.environ.get("DEFAULT_SHEET_ID", "1pdRt04-OgUvEQJLXqZXBtZJO2Cs7Yg4KwfmW7k7heYg")
        u_sheet_id_input = st.text_input("4. Google Sheet ID", value=u_default_sheet_id, key="u_sheet_id")
    with u_col_s2:
        u_tab_name_input = st.text_input("5. Sheet Tab Name", value="Sheet1", key="u_tab_name")

    with st.expander("⚙️ Developer options"):
        u_use_fixture_cb = st.checkbox(
            "Force fixture for Sheet audit (skip LLM extraction, use hardcoded Unit 313 data)",
            value=_sheet_prompt_is_placeholder(),
            key="u_use_fixture",
        )

    if st.button("🚀 Run Unified Verification", type="primary", key="run_unified"):
        if not u_afs_file or not u_aadhaar_files or not u_pan_files:
            st.warning("⚠️ Please upload the AFS, Aadhaar Card(s), and PAN Card(s) to proceed.")
        elif not u_sheet_id_input.strip():
            st.warning("⚠️ Please enter a Google Sheet ID.")
        elif not u_tab_name_input.strip():
            st.warning("⚠️ Please enter the sheet tab name.")
        else:
            with st.spinner("🤖 Running KYC verification and Sheet audit... This may take a minute."):
                try:
                    u_aadhaar_list = [
                        {"bytes": f.getvalue(), "mime": f.type, "filename": f.name}
                        for f in u_aadhaar_files
                    ]
                    u_pan_list = [
                        {"bytes": f.getvalue(), "mime": f.type, "filename": f.name}
                        for f in u_pan_files
                    ]
                    unified = verify_unified(
                        afs_bytes=u_afs_file.getvalue(),
                        afs_mime=u_afs_file.type,
                        aadhaar_list=u_aadhaar_list,
                        pan_list=u_pan_list,
                        sheet_id=u_sheet_id_input.strip(),
                        tab=u_tab_name_input.strip(),
                        afs_filename=u_afs_file.name,
                        use_fixture=u_use_fixture_cb,
                    )
                except Exception as e:
                    st.error(f"An error occurred during unified verification: {e}")
                    st.stop()

            st.success("Unified Verification Complete!")

            # ── KYC section ────────────────────────────────────────────────
            st.markdown("## 🆔 KYC Verification")
            kyc = unified.get("kyc", {})
            if "error" in kyc:
                st.error(f"❌ KYC verification failed: {kyc['error']}")
            else:
                render_kyc_result(kyc["report_text"], kyc["json_data"], crm_email, key_prefix="unified")

            st.markdown("---")

            # ── Sheet audit section ────────────────────────────────────────
            st.markdown("## 🔢 AFS ↔ Sheet Audit")
            sheet = unified.get("sheet", {})
            if "error" in sheet:
                st.error(f"❌ Sheet audit failed: {sheet['error']}")
            else:
                render_sheet_result(
                    sheet,
                    sheet_id=u_sheet_id_input.strip(),
                    tab_name=u_tab_name_input.strip(),
                    afs_filename=u_afs_file.name,
                    crm_email=crm_email,
                    key_prefix="unified",
                )

with tab1:
    col1, col2, col3 = st.columns(3)

    with col1:
        afs_file = st.file_uploader("1. Agreement for Sale (AFS)", type=["pdf"])

    with col2:
        aadhaar_files = st.file_uploader("2. Aadhaar Card(s) (Upload multiple for co-applicants)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

    with col3:
        pan_files = st.file_uploader("3. PAN Card(s) (Upload multiple for co-applicants)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

    if st.button("🔍 Run Verification", type="primary"):
        if not afs_file or not aadhaar_files or not pan_files:
            st.warning("⚠️ Please upload all required documents (AFS, Aadhaar Card(s), PAN Card(s)) to proceed.")
        else:
            with st.spinner("🤖 Agent is analyzing documents... This may take a minute."):
                try:
                    # Prepare lists of uploaded KYC documents
                    aadhaar_list = []
                    for f in aadhaar_files:
                        aadhaar_list.append({
                            "bytes": f.getvalue(),
                            "mime": f.type,
                            "filename": f.name
                        })

                    pan_list = []
                    for f in pan_files:
                        pan_list.append({
                            "bytes": f.getvalue(),
                            "mime": f.type,
                            "filename": f.name
                        })

                    report_text, json_data = verify_documents(
                        afs_bytes=afs_file.getvalue(),
                        afs_mime=afs_file.type,
                        aadhaar_list=aadhaar_list,
                        pan_list=pan_list,
                        afs_filename=afs_file.name
                    )

                    st.success("Verification Complete!")
                    render_kyc_result(report_text, json_data, crm_email, key_prefix="tab1")

                except Exception as e:
                    st.error(f"An error occurred during verification: {e}")

with tab2:
    st.header("AFS ↔ Google Sheet Audit")
    st.markdown(
        "Upload an AFS PDF and verify its **Agreement Value, Unit Number, Area Sq.M, and Area Sq.Ft** "
        "against the correct row in your Google Sheet. Python does all normalization and comparison — no LLM math."
    )

    # ── Prompt status notice ────────────────────────────────────────────────
    if _sheet_prompt_is_placeholder():
        st.warning(
            "⚙️ **Extraction prompt not configured.** "
            "`afs_sheet_system_prompt.md` is still a placeholder. "
            "The app will use the **built-in fixture** (Unit 313 / Rs.99,77,517 / 42.06 Sq.M / 453 Sq.Ft) "
            "instead of calling the LLM. Replace the file with a real prompt to enable live extraction."
        )

    # ── Inputs ─────────────────────────────────────────────────────────────
    sheet_afs_file = st.file_uploader(
        "1. Agreement for Sale (AFS)", type=["pdf"], key="sheet_afs"
    )

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        default_sheet_id = os.environ.get("DEFAULT_SHEET_ID", "1pdRt04-OgUvEQJLXqZXBtZJO2Cs7Yg4KwfmW7k7heYg")
        sheet_id_input = st.text_input("2. Google Sheet ID", value=default_sheet_id)
    with col_s2:
        tab_name_input = st.text_input("3. Sheet Tab Name", value="Sheet1")

    with st.expander("⚙️ Developer options"):
        use_fixture_cb = st.checkbox(
            "Force fixture (skip LLM extraction, use hardcoded Unit 313 data)",
            value=_sheet_prompt_is_placeholder(),
        )

    if st.button("🔍 Run Sheet Audit", type="primary", key="run_sheet_audit"):
        if not sheet_afs_file:
            st.warning("⚠️ Please upload an AFS PDF to proceed.")
        elif not sheet_id_input.strip():
            st.warning("⚠️ Please enter a Google Sheet ID.")
        elif not tab_name_input.strip():
            st.warning("⚠️ Please enter the sheet tab name.")
        else:
            with st.spinner("🤖 Extracting from AFS and fetching sheet row..."):
                try:
                    result = verify_afs_against_sheet(
                        afs_bytes=sheet_afs_file.getvalue(),
                        afs_filename=sheet_afs_file.name,
                        sheet_id=sheet_id_input.strip(),
                        tab=tab_name_input.strip(),
                        use_fixture=use_fixture_cb,
                    )
                except Exception as e:
                    st.error(f"❌ Audit failed: {e}")
                    st.stop()

            render_sheet_result(
                result,
                sheet_id=sheet_id_input.strip(),
                tab_name=tab_name_input.strip(),
                afs_filename=sheet_afs_file.name,
                crm_email=crm_email,
                key_prefix="tab2",
            )


with tab3:
    st.header("Past Verifications")
    df = get_all_verifications_df()

    if df.empty:
        st.info("No past verifications found. Run a verification to see it here!")
    else:
        # Show table with formatted status and columns
        df_display = df.copy()
        df_display["status"] = df_display["status"].map({
            "MATCH": "✅ MATCH",
            "MISMATCH": "❌ MISMATCH"
        }).fillna(df_display["status"])

        st.dataframe(
            df_display,
            column_config={
                "id": None,  # Hide ID column
                "date": "Date & Time",
                "buyer_name": "Buyer Name",
                "project_name": "Project Name",
                "unit_number": "Unit/Flat",
                "status": "Status"
            },
            use_container_width=True,
            hide_index=True
        )

        st.markdown("---")
        st.subheader("View Full Report")

        # Let user select a record to view the full text
        record_options = [f"ID {row['id']} - {row['buyer_name']} ({row['date']})" for _, row in df.iterrows()]
        selected_record = st.selectbox("Select a record to view its full report:", record_options)

        if selected_record:
            # Extract ID from the selection
            record_id = int(selected_record.split(" ")[1])
            report_text = get_report_by_id(record_id)

            # Extract buyer name from selected record text for cleaner file name
            try:
                name_part = selected_record.split(" - ")[1].split(" (")[0]
            except Exception:
                name_part = f"Record_{record_id}"

            # Reconstruct minimal json_data from the history row for the cover page
            history_row = df[df["id"] == record_id].iloc[0]
            history_json = {
                "buyer_name": history_row.get("buyer_name", name_part),
                "project_name": history_row.get("project_name", "—"),
                "unit_number": history_row.get("unit_number", "—"),
                "afs_date": history_row.get("afs_date", "—"),
                "status": history_row.get("status", "MISMATCH"),
            }
            # Generate PDF for download
            pdf_bytes = generate_pdf_report(report_text, name_part, json_data=history_json)
            st.download_button(
                label="📥 Download Report as PDF",
                data=pdf_bytes,
                file_name=f"KYC_Report_{name_part.replace(' ', '_')}.pdf",
                mime="application/pdf",
                key=f"download_history_{record_id}"
            )
            st.markdown(report_text)

    # ── Sheet audit history ──────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📊 Sheet Audit History")
    df_audits = get_all_sheet_audits_df()

    if df_audits.empty:
        st.info("No sheet audits yet. Run an audit in the 'AFS ↔ Sheet Audit' tab.")
    else:
        df_audits_display = df_audits.copy()
        df_audits_display["verdict"] = df_audits_display["verdict"].map(
            {"PASS": "✅ PASS", "FAIL": "❌ FAIL"}
        ).fillna(df_audits_display["verdict"])

        st.dataframe(
            df_audits_display,
            column_config={
                "id": None,
                "date": "Date & Time",
                "unit_no": "Unit No.",
                "buyer_name": "Buyer",
                "project_name": "Project",
                "verdict": "Verdict",
            },
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("**View Full Audit Record**")
        audit_options = [
            f"ID {row['id']} — Unit {row['unit_no']} | {row['buyer_name']} ({row['date']})"
            for _, row in df_audits.iterrows()
        ]
        selected_audit = st.selectbox("Select an audit record:", audit_options, key="audit_select")

        if selected_audit:
            audit_id = int(selected_audit.split(" ")[1])
            audit_record = get_sheet_audit_by_id(audit_id)
            if audit_record:
                audit_fields = audit_record.get("per_field_json", [])
                STATUS_EMOJI_H = {
                    "MATCH": "✅ MATCH", "MISMATCH": "❌ MISMATCH",
                    "SCHEMA_CAVEAT": "⚠️ CAVEAT", "INTERNAL_DISCREPANCY": "🔴 DISCREPANCY",
                    "NOT_FOUND_IN_AFS": "❓ NOT IN AFS", "NOT_FOUND_IN_SHEET": "❓ NOT IN SHEET",
                    "INFO_ONLY": "ℹ️ INFO ONLY",
                }
                rows_hist = []
                for f in audit_fields:
                    rows_hist.append({
                        "Status": STATUS_EMOJI_H.get(f.get("status", ""), f.get("status", "")),
                        "Field": f.get("field_name", ""),
                        "AFS Value": " | ".join(str(v) for v in f.get("afs_distinct_values", [])),
                        "Sheet Raw": str(f.get("sheet_raw", "")),
                        "AFS Norm.": str(f.get("afs_normalized") or "—"),
                        "Sheet Norm.": str(f.get("sheet_normalized") or "—"),
                        "Notes": f.get("detail", ""),
                    })
                if rows_hist:
                    st.dataframe(pd.DataFrame(rows_hist), use_container_width=True, hide_index=True)


# ── Tab 4: Update KYC -> Sheet (extract ID docs, write into a Unit row) ────────
# Additive feature: extracts Aadhaar/PAN/Passport fields and writes them into
# the matching Unit No. row of an existing Google Sheet. Does not touch any
# other tab's logic, state, or history.

ID_FIELD_LABELS = {
    "name": "Full Name",
    "pan": "PAN Number",
    "aadhaar": "Aadhaar Number",
    "passport": "Passport Number",
    "email": "Email",
    "address": "Address",
    "contact": "Contact No.",
}

with tab4:
    st.header("🪪 Update KYC Documents → Google Sheet")
    st.markdown(
        "Upload Aadhaar / PAN / Passport for the applicant (and optionally a co-applicant), "
        "enter the Unit No., and the extracted details will be written into the matching row "
        "of your Google Sheet. **Nothing is written until you review and click Save.**"
    )

    id_default_sheet_id = os.environ.get("DEFAULT_SHEET_ID", "")
    id_col1, id_col2, id_col3 = st.columns(3)
    with id_col1:
        id_unit_no = st.text_input("Unit No.", key="id_unit_no")
    with id_col2:
        id_sheet_id = st.text_input("Google Sheet ID", value=id_default_sheet_id, key="id_sheet_id")
    with id_col3:
        id_tab_name = st.text_input("Sheet Tab Name", value="Inventory Sheet", key="id_tab_name")

    st.subheader("Primary Applicant")
    p_col1, p_col2, p_col3 = st.columns(3)
    with p_col1:
        id_p_aadhaar = st.file_uploader("Aadhaar Card", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key="id_p_aadhaar")
    with p_col2:
        id_p_pan = st.file_uploader("PAN Card", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key="id_p_pan")
    with p_col3:
        id_p_passport = st.file_uploader("Passport", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key="id_p_passport")

    st.subheader("Co-Applicant (optional)")
    c_col1, c_col2, c_col3 = st.columns(3)
    with c_col1:
        id_c_aadhaar = st.file_uploader("Aadhaar Card", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key="id_c_aadhaar")
    with c_col2:
        id_c_pan = st.file_uploader("PAN Card", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key="id_c_pan")
    with c_col3:
        id_c_passport = st.file_uploader("Passport", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key="id_c_passport")

    def _to_doc_list(files):
        return [{"bytes": f.getvalue(), "mime": f.type, "filename": f.name} for f in (files or [])]

    if st.button("🔍 Extract Details", key="id_extract_btn"):
        has_primary_docs = bool(id_p_aadhaar or id_p_pan or id_p_passport)
        if not has_primary_docs:
            st.warning("⚠️ Upload at least one document for the primary applicant.")
        else:
            with st.spinner("🤖 Extracting identity fields..."):
                try:
                    primary_extracted = extract_id_documents(
                        _to_doc_list(id_p_aadhaar), _to_doc_list(id_p_pan), _to_doc_list(id_p_passport),
                    )
                    st.session_state["id_update_primary"] = primary_extracted

                    has_co_docs = bool(id_c_aadhaar or id_c_pan or id_c_passport)
                    if has_co_docs:
                        co_extracted = extract_id_documents(
                            _to_doc_list(id_c_aadhaar), _to_doc_list(id_c_pan), _to_doc_list(id_c_passport),
                        )
                        st.session_state["id_update_co"] = co_extracted
                    else:
                        st.session_state["id_update_co"] = {}
                    st.toast("✅ Extraction complete — review below before saving.")
                except Exception as e:
                    st.error(f"❌ Extraction failed: {e}")

    if "id_update_primary" in st.session_state:
        st.markdown("---")
        st.subheader("Review & Edit Extracted Values")
        st.caption("Edit any field below if the extraction got something wrong. Blank fields are left untouched in the sheet.")

        st.markdown("**Primary Applicant**")
        primary_edited = {}
        for key, label in ID_FIELD_LABELS.items():
            primary_edited[key] = st.text_input(
                f"{label} (Primary)",
                value=st.session_state["id_update_primary"].get(key, ""),
                key=f"id_edit_primary_{key}",
            )

        co_edited = {}
        if st.session_state.get("id_update_co"):
            st.markdown("**Co-Applicant**")
            for key, label in ID_FIELD_LABELS.items():
                if key not in CO_APPLICANT_FIELD_MAP:
                    continue
                co_edited[key] = st.text_input(
                    f"{label} (Co-Applicant)",
                    value=st.session_state["id_update_co"].get(key, ""),
                    key=f"id_edit_co_{key}",
                )

        if st.button("💾 Save to Sheet", type="primary", key="id_save_btn"):
            if not id_unit_no.strip():
                st.warning("⚠️ Please enter the Unit No.")
            elif not id_sheet_id.strip():
                st.warning("⚠️ Please enter a Google Sheet ID.")
            elif not id_tab_name.strip():
                st.warning("⚠️ Please enter the sheet tab name.")
            else:
                field_values = {}
                for key, value in primary_edited.items():
                    col = PRIMARY_APPLICANT_FIELD_MAP.get(key)
                    if col and value.strip():
                        field_values[col] = value.strip()
                for key, value in co_edited.items():
                    col = CO_APPLICANT_FIELD_MAP.get(key)
                    if col and value.strip():
                        field_values[col] = value.strip()

                if not field_values:
                    st.warning("⚠️ No non-empty fields to write.")
                else:
                    try:
                        result = update_unit_row(
                            sheet_id=id_sheet_id.strip(),
                            tab=id_tab_name.strip(),
                            unit_no=id_unit_no.strip(),
                            field_values=field_values,
                        )
                        st.success(
                            f"✅ Updated row {result['row']} — columns: {', '.join(result['updated_columns'])}"
                        )
                        try:
                            append_kyc_log(
                                buyer_name=primary_edited.get("name", "Unknown"),
                                project_name=id_tab_name.strip(),
                                unit_number=id_unit_no.strip(),
                                status="SHEET_UPDATED",
                                report_text=f"Updated columns: {', '.join(result['updated_columns'])}",
                            )
                        except Exception as log_err:
                            st.warning(f"⚠️ Could not write audit log: {log_err}")
                    except Exception as e:
                        st.error(f"❌ Save failed: {e}")
