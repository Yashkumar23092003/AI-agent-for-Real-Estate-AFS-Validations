import streamlit as st
import os
import pandas as pd
from agent import verify_documents, verify_afs_against_sheet, _sheet_prompt_is_placeholder
from notifier import generate_match_email, generate_mismatch_email, generate_sheet_audit_email
from database import (
    init_db, save_verification, get_all_verifications_df, get_report_by_id,
    save_sheet_audit, get_all_sheet_audits_df, get_sheet_audit_by_id,
)
from pdf_report import generate_pdf_report, generate_sheet_audit_pdf
from comparator import MATCH, MISMATCH, SCHEMA_CAVEAT, INTERNAL_DISCREPANCY, NOT_FOUND_IN_AFS, NOT_FOUND_IN_SHEET

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

tab1, tab2, tab3 = st.tabs(["🆕 KYC Verification", "🔢 AFS ↔ Sheet Audit", "📚 Verification History"])

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
                    
                    # Handle Email Trigger and Data Extraction
                    st.subheader("Action Taken")
                    status = json_data.get("status", "MISMATCH")
                    buyer_name = json_data.get("buyer_name", "Unknown Client")
                    project_name = json_data.get("project_name", "Unknown Project")
                    unit_number = json_data.get("unit_number", "Unknown Unit")
                    afs_date = json_data.get("afs_date", "Unknown Date")
                    
                    # Save to history database
                    save_verification(buyer_name, project_name, unit_number, status, report_text)
                    st.toast("💾 Record saved to History!")
                    
                    if status == "MATCH":
                        st.info("✅ All fields match. Triggering success email to CRM.")
                        success = generate_match_email(
                            crm_email=crm_email,
                            buyer_name=buyer_name,
                            project_name=project_name,
                            unit_number=unit_number,
                            afs_date=afs_date,
                            report_text=report_text
                        )
                    else:
                        st.error("❌ Mismatches found. Triggering action-required email to CRM.")
                        success = generate_mismatch_email(
                            crm_email=crm_email,
                            buyer_name=buyer_name,
                            project_name=project_name,
                            unit_number=unit_number,
                            afs_date=afs_date,
                            mismatches_text=json_data.get("mismatches_text", "Please review the attached report for mismatches."),
                            report_text=report_text
                        )
                    
                    if success:
                        st.toast("📧 Email successfully sent to CRM!")
                    else:
                        st.warning("⚠️ Could not send email. Please check your SMTP credentials in the .env file.")
                        
                    # Display the report
                    st.subheader("Verification Report")
                    # Generate PDF for download
                    pdf_bytes = generate_pdf_report(report_text, buyer_name, json_data=json_data)
                    st.download_button(
                        label="📥 Download Report as PDF",
                        data=pdf_bytes,
                        file_name=f"KYC_Report_{buyer_name.replace(' ', '_')}.pdf",
                        mime="application/pdf",
                        key="download_new_report"
                    )
                    st.markdown(report_text)
                        
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
                return ""

            styled = df_fields.style.map(_colour_status, subset=["Status"])
            st.dataframe(styled, use_container_width=True, hide_index=True)

            # ── Save to history ─────────────────────────────────────────────
            save_sheet_audit(
                unit_no=unit_no,
                buyer_name=buyer_name,
                project_name=project_name,
                sheet_id=sheet_id_input.strip(),
                tab_name=tab_name_input.strip(),
                verdict=verdict,
                fields=fields,
                afs_filename=sheet_afs_file.name,
            )
            st.toast("💾 Audit record saved to History!")

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
                st.warning("⚠️ Could not send email. Check SMTP credentials in .env.")

            # ── PDF download ─────────────────────────────────────────────────
            pdf_bytes = generate_sheet_audit_pdf(result)
            st.download_button(
                label="📥 Download Audit Report as PDF",
                data=pdf_bytes,
                file_name=f"SheetAudit_{unit_no}_{buyer_name.replace(' ', '_')}.pdf",
                mime="application/pdf",
                key="download_sheet_audit",
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
