import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import markdown
from dotenv import load_dotenv

load_dotenv()

def build_html_report_email(buyer_name, project_name, unit_number, afs_date, status, report_markdown):
    """
    Converts markdown report to a beautifully designed, responsive HTML email body.
    """
    # Convert markdown to HTML with table extension enabled
    html_content = markdown.markdown(report_markdown, extensions=['tables'])
    
    # Inline/replace emojis with nice styled badges
    html_content = html_content.replace('<td>✅</td>', '<td style="text-align: center; white-space: nowrap;"><span style="background-color: #d1fae5; color: #065f46; padding: 4px 8px; border-radius: 9999px; font-size: 12px; font-weight: 600; display: inline-block;">✅ MATCH</span></td>')
    html_content = html_content.replace('<td>❌</td>', '<td style="text-align: center; white-space: nowrap;"><span style="background-color: #fee2e2; color: #991b1b; padding: 4px 8px; border-radius: 9999px; font-size: 12px; font-weight: 600; display: inline-block;">❌ MISMATCH</span></td>')
    html_content = html_content.replace('<td>⚠️</td>', '<td style="text-align: center; white-space: nowrap;"><span style="background-color: #fef3c7; color: #92400e; padding: 4px 8px; border-radius: 9999px; font-size: 12px; font-weight: 600; display: inline-block;">⚠️ REVIEW</span></td>')

    # Color scheme based on status
    if status == "MATCH":
        status_color = "#10b981"
        status_bg = "#ecfdf5"
        status_border = "#a7f3d0"
        status_text = "✅ ALL FIELDS MATCH"
    else:
        status_color = "#ef4444"
        status_bg = "#fef2f2"
        status_border = "#fca5a5"
        status_text = "❌ MISMATCH DETECTED"

    email_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>KYC Verification Report</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            color: #334155;
            background-color: #f8fafc;
            margin: 0;
            padding: 20px;
        }}
        .email-container {{
            max-width: 800px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
            border: 1px solid #e2e8f0;
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            color: #ffffff;
            padding: 28px 24px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0;
            font-size: 22px;
            font-weight: 700;
            letter-spacing: -0.025em;
        }}
        .content {{
            padding: 24px;
        }}
        .status-banner {{
            padding: 16px;
            border-radius: 8px;
            margin-bottom: 24px;
            font-weight: 700;
            font-size: 16px;
            text-align: center;
            background-color: {status_bg};
            color: {status_color};
            border: 1px solid {status_border};
        }}
        .metadata-box {{
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 24px;
        }}
        .metadata-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 0 !important;
        }}
        .metadata-table td {{
            padding: 6px 12px !important;
            border: none !important;
            font-size: 14px;
            background: none !important;
        }}
        .metadata-label {{
            font-weight: 600;
            color: #64748b;
        }}
        
        /* Table Styling */
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            font-size: 13px;
        }}
        th {{
            background-color: #f1f5f9;
            color: #475569;
            font-weight: 600;
            text-align: left;
            padding: 12px 14px;
            border-bottom: 2px solid #e2e8f0;
        }}
        td {{
            padding: 12px 14px;
            border-bottom: 1px solid #e2e8f0;
            color: #334155;
        }}
        tr:nth-child(even) {{
            background-color: #f8fafc;
        }}
        
        /* Typography */
        h1, h2, h3 {{
            color: #1e293b;
        }}
        h2 {{
            font-size: 18px;
            border-bottom: 2px solid #f1f5f9;
            padding-bottom: 8px;
            margin-top: 28px;
        }}
        h3 {{
            font-size: 15px;
            margin-top: 20px;
        }}
        p, li {{
            line-height: 1.6;
            font-size: 14px;
        }}
        ul {{
            padding-left: 20px;
        }}
        .footer {{
            background-color: #f8fafc;
            padding: 16px;
            text-align: center;
            font-size: 12px;
            color: #64748b;
            border-top: 1px solid #e2e8f0;
        }}
    </style>
</head>
<body>
    <div class="email-container">
        <div class="header">
            <h1>📋 Real Estate KYC Verification</h1>
        </div>
        <div class="content">
            <div class="status-banner">
                {status_text}
            </div>
            
            <div class="metadata-box">
                <table class="metadata-table">
                    <tr>
                        <td style="width: 50%;"><span class="metadata-label">Primary Buyer:</span> {buyer_name}</td>
                        <td style="width: 50%;"><span class="metadata-label">Project Name:</span> {project_name}</td>
                    </tr>
                    <tr>
                        <td><span class="metadata-label">Unit / Flat:</span> {unit_number}</td>
                        <td><span class="metadata-label">AFS Date:</span> {afs_date}</td>
                    </tr>
                </table>
            </div>
            
            {html_content}
            
        </div>
        <div class="footer">
            This verification was performed automatically by the KYC Verification Agent.<br>
            Please do not reply to this email. For technical support, contact the IT team.
        </div>
    </div>
</body>
</html>
"""
    return email_html

def send_verification_email(to_email: str, subject: str, html_body: str, plain_body: str = ""):
    """
    Sends an email using standard SMTP, supporting both Plain Text and HTML.
    Requires SMTP_EMAIL and SMTP_PASSWORD in .env.
    """
    sender_email = os.environ.get("SMTP_EMAIL")
    sender_password = os.environ.get("SMTP_PASSWORD")
    
    if not sender_email or not sender_password:
        print("Warning: SMTP credentials not set. Email not sent.")
        return False
        
    msg = MIMEMultipart('alternative')
    msg['From'] = sender_email
    msg['To'] = to_email
    msg['Subject'] = subject
    
    if plain_body:
        msg.attach(MIMEText(plain_body, 'plain'))
    else:
        msg.attach(MIMEText("Please enable HTML viewing to see the verification report.", 'plain'))
        
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        text = msg.as_string()
        server.sendmail(sender_email, to_email, text)
        server.quit()
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

def generate_match_email(crm_email, buyer_name, project_name, unit_number, afs_date, report_text=""):
    subject = f"✅ KYC Verified — {buyer_name} | {project_name} | {unit_number}"
    
    plain_body = f"""Dear CRM Team,

This is an automated KYC verification result for the following client:

CLIENT: {buyer_name}
PROPERTY: {unit_number}, {project_name}
AFS DATE: {afs_date}

KYC VERIFICATION STATUS: ✅ ALL FIELDS MATCH

Please view this email in an HTML-capable email client to view the complete verification table.

Regards,
KYC Verification Agent
"""
    # If report_text is provided, we build the beautiful HTML email. Otherwise, fallback.
    if report_text:
        html_body = build_html_report_email(buyer_name, project_name, unit_number, afs_date, "MATCH", report_text)
    else:
        html_body = f"<p>All fields matched for client <strong>{buyer_name}</strong>.</p>"
        
    return send_verification_email(crm_email, subject, html_body, plain_body)

def generate_mismatch_email(crm_email, buyer_name, project_name, unit_number, afs_date, mismatches_text, report_text=""):
    subject = f"❌ KYC MISMATCH — ACTION REQUIRED | {buyer_name} | {project_name} | {unit_number}"

    plain_body = f"""Dear CRM Team,

⚠️ URGENT — KYC VERIFICATION FAILED

This is an automated KYC verification result for the following client.
Discrepancies have been found between the Agreement for Sale and the original KYC documents.

CLIENT: {buyer_name}
PROPERTY: {unit_number}, {project_name}
AFS DATE: {afs_date}

KYC VERIFICATION STATUS: ❌ MISMATCH DETECTED

=== MISMATCH DETAILS ===
{mismatches_text}

Please view this email in an HTML-capable email client to view the complete verification table.

Regards,
KYC Verification Agent
"""
    if report_text:
        html_body = build_html_report_email(buyer_name, project_name, unit_number, afs_date, "MISMATCH", report_text)
    else:
        html_body = f"<p>Mismatches detected for client <strong>{buyer_name}</strong>: {mismatches_text}</p>"

    return send_verification_email(crm_email, subject, html_body, plain_body)


# ── AFS ↔ Sheet audit email ───────────────────────────────────────────────────

def _build_sheet_audit_html(buyer_name, unit_no, project_name, verdict, fields, warnings):
    """Builds a styled HTML email body for a sheet audit result."""
    if verdict == "PASS":
        status_color, status_bg, status_border = "#10b981", "#ecfdf5", "#a7f3d0"
        status_text = "✅ ALL FIELDS VERIFIED — PASS"
    else:
        status_color, status_bg, status_border = "#ef4444", "#fef2f2", "#fca5a5"
        status_text = "❌ VERIFICATION FAILED — MISMATCH DETECTED"

    rows_html = ""
    for f in fields:
        if f["status"] == "MATCH":
            badge = '<span style="background:#d1fae5;color:#065f46;padding:3px 8px;border-radius:9999px;font-size:11px;font-weight:600;">✅ MATCH</span>'
        elif f["status"] == "SCHEMA_CAVEAT":
            badge = '<span style="background:#fef3c7;color:#92400e;padding:3px 8px;border-radius:9999px;font-size:11px;font-weight:600;">⚠️ CAVEAT</span>'
        else:
            badge = f'<span style="background:#fee2e2;color:#991b1b;padding:3px 8px;border-radius:9999px;font-size:11px;font-weight:600;">❌ {f["status"]}</span>'

        afs_val = f.get("afs_normalized") or " | ".join(str(v) for v in f.get("afs_distinct_values", []))
        sheet_val = f.get("sheet_normalized") or f.get("sheet_raw") or "—"
        detail = f.get("detail") or ""

        rows_html += f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">{badge}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;font-weight:600;">{f["field_name"]}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;font-family:monospace;">{afs_val}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;font-family:monospace;">{sheet_val}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;font-size:12px;color:#64748b;">{detail}</td>
        </tr>"""

    warnings_html = ""
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings)
        warnings_html = f'<p style="color:#92400e;background:#fef3c7;padding:12px;border-radius:6px;font-size:13px;"><strong>⚠️ Warnings:</strong><ul>{items}</ul></p>'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>AFS Sheet Audit</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#334155;background:#f8fafc;margin:0;padding:20px;">
  <div style="max-width:800px;margin:0 auto;background:#fff;border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,.08);border:1px solid #e2e8f0;overflow:hidden;">
    <div style="background:linear-gradient(135deg,#1e293b,#0f172a);color:#fff;padding:24px;text-align:center;">
      <h1 style="margin:0;font-size:20px;font-weight:700;">AFS ↔ Google Sheet Audit</h1>
    </div>
    <div style="padding:24px;">
      <div style="padding:14px;border-radius:8px;margin-bottom:20px;font-weight:700;font-size:15px;text-align:center;
                  background-color:{status_bg};color:{status_color};border:1px solid {status_border};">
        {status_text}
      </div>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin-bottom:20px;font-size:14px;">
        <strong>Buyer:</strong> {buyer_name} &nbsp;|&nbsp;
        <strong>Unit No.:</strong> {unit_no} &nbsp;|&nbsp;
        <strong>Project:</strong> {project_name}
      </div>
      {warnings_html}
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#f1f5f9;">
            <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;">Status</th>
            <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;">Field</th>
            <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;">AFS Value</th>
            <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;">Sheet Value</th>
            <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;">Notes</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
    <div style="background:#f8fafc;padding:14px;text-align:center;font-size:11px;color:#64748b;border-top:1px solid #e2e8f0;">
      Auto-generated by KYC Verification Agent. Do not reply to this email.
    </div>
  </div>
</body>
</html>"""


def generate_sheet_audit_email(crm_email, buyer_name, unit_no, project_name,
                                verdict, fields, warnings):
    """
    Sends a sheet audit result email (both PASS and FAIL).
    'fields' must be a list of dicts (from database.save_sheet_audit serialisation
    or from FieldResult.__dict__).
    """
    verdict_label = "PASS ✅" if verdict == "PASS" else "FAIL ❌"
    subject = f"AFS Sheet Audit {verdict_label} — Unit {unit_no} | {project_name}"

    plain_body = (
        f"AFS ↔ Sheet Verification Result\n\n"
        f"Buyer:   {buyer_name}\n"
        f"Unit:    {unit_no}\n"
        f"Project: {project_name}\n"
        f"Verdict: {verdict}\n\n"
        "Please view this email in an HTML-capable client for the full field table.\n\n"
        "Regards,\nKYC Verification Agent"
    )

    # Normalise fields to dicts (accept both dataclass instances and plain dicts)
    fields_dicts = []
    for f in fields:
        if isinstance(f, dict):
            fields_dicts.append(f)
        else:
            fields_dicts.append({
                "field_name": f.field_name,
                "status": f.status,
                "afs_distinct_values": f.afs_distinct_values,
                "sheet_raw": f.sheet_raw,
                "afs_normalized": f.afs_normalized,
                "sheet_normalized": f.sheet_normalized,
                "detail": f.detail,
            })

    html_body = _build_sheet_audit_html(buyer_name, unit_no, project_name,
                                         verdict, fields_dicts, warnings)
    return send_verification_email(crm_email, subject, html_body, plain_body)
