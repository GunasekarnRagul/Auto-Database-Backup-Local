# -*- coding: utf-8 -*-
import io
import zipfile
import logging
import werkzeug

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class NextcloudController(http.Controller):

    # ─── OAuth callback (legacy, kept for backward compat) ──────────────────────

    @http.route("/nextcloud/authentication", type="http", auth="user")
    def nextcloud_oauth2callback(self, **kw):
        code = kw.get("code")
        state = kw.get("state")
        if not code:
            return "No code provided"
        if not state:
            return "No state (config ID) provided"
        config = request.env["nextcloud.config"].sudo().browse(int(state))
        if not config:
            return "Invalid Configuration ID"
        return "OAuth flow not used in AWS S3 mode."

    # ─── Internal S3 helper ─────────────────────────────────────────────────────

    def _get_s3_object(self, file_record):
        """
        Fetch file bytes from AWS S3 using boto3.

        ``nextcloud_file_id`` stores the S3 object key with a leading '/'
        (e.g. '/root-folder/subfolder/report.pdf').  Strip the leading slash
        to obtain the real S3 bucket key before calling get_object.

        Returns (content_bytes, content_type) or raises on failure.
        """
        import mimetypes
        config = file_record.drive_config_id
        sync_service = request.env["nextcloud.sync"].sudo()
        s3 = sync_service._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or "").strip()

        s3_key = (file_record.nextcloud_file_id or "").lstrip("/")
        if not s3_key:
            raise ValueError(
                f"File '{file_record.name}' has no S3 key (nextcloud_file_id is empty). "
                "Sync the file first."
            )

        resp = s3.get_object(Bucket=bucket, Key=s3_key)
        content = resp["Body"].read()

        guessed_mime, _ = mimetypes.guess_type(file_record.name)
        content_type = file_record.mime_type or guessed_mime or "application/octet-stream"
        return content, content_type

    # ─── "Not synced yet" HTML helper ───────────────────────────────────────────

    @staticmethod
    def _not_synced_html(file_name, record_id, action="preview"):
        return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>File Not Synced</title></head>
<body style="font-family:sans-serif;display:flex;justify-content:center;
             align-items:center;height:100vh;margin:0;background:#f8f9fa;">
  <div style="text-align:center;padding:2rem;background:white;border-radius:8px;
              box-shadow:0 4px 6px rgba(0,0,0,.1);max-width:480px;">
    <h2 style="margin:0 0 1rem;color:#343a40;">{'Preview' if action=='preview' else 'Download'} Not Available</h2>
    <p style="color:#6c757d;margin-bottom:1.5rem;">
      <strong>{file_name}</strong> has not been uploaded to AWS S3 yet.<br>
      Please click <em>Sync</em> in the File Explorer to upload it first.
    </p>
  </div>
</body>
</html>"""

    # ─── Open in AWS S3 (presigned redirect) ────────────────────────────────────

    @http.route("/nextcloud/open/<int:file_id>", type="http", auth="user")
    def nextcloud_open_in_s3(self, file_id, **kw):
        """
        Generate a short-lived S3 presigned URL and redirect the browser to it.
        This opens the file directly from S3 in a new tab — images and PDFs will
        render inline; other types will download via S3 natively.
        """
        file_record = request.env["nextcloud.file"].sudo().browse(file_id)
        if not file_record.exists():
            return request.not_found()

        if not file_record.nextcloud_file_id:
            html = self._not_synced_html(file_record.name, file_id, action="preview")
            return request.make_response(html, headers=[("Content-Type", "text/html")])

        try:
            config = file_record.drive_config_id
            sync_service = request.env["nextcloud.sync"].sudo()
            s3 = sync_service._get_s3_client(config)
            bucket = (config.aws_s3_bucket_name or "").strip()
            s3_key = (file_record.nextcloud_file_id or "").lstrip("/")

            if not s3_key:
                raise ValueError("No S3 key stored for this file.")

            import mimetypes
            guessed_mime, _ = mimetypes.guess_type(file_record.name)
            content_type = file_record.mime_type or guessed_mime or "application/octet-stream"

            # Build ResponseContentDisposition so the browser opens inline when possible
            content_disposition = f'inline; filename="{file_record.name}"'

            presigned_url = s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket,
                    "Key": s3_key,
                    "ResponseContentType": content_type,
                    "ResponseContentDisposition": content_disposition,
                },
                ExpiresIn=3600,  # 1 hour
            )

            _logger.info("Open-in-S3 presigned redirect for '%s'", file_record.name)
            return werkzeug.utils.redirect(presigned_url, code=302)

        except Exception as exc:
            _logger.error("S3 open error for '%s': %s", file_record.name, exc)
            html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Open Error</title></head>
<body style="font-family:sans-serif;display:flex;justify-content:center;
             align-items:center;height:100vh;margin:0;background:#f8f9fa;">
  <div style="text-align:center;padding:2rem;background:white;border-radius:8px;
              box-shadow:0 4px 6px rgba(0,0,0,.1);max-width:480px;">
    <h2 style="margin:0 0 1rem;color:#dc3545;">Cannot Open File</h2>
    <p style="color:#6c757d;margin-bottom:1.5rem;">{exc}</p>
    <a href="/nextcloud/download/{file_record.id}"
       style="display:inline-block;padding:.5rem 1rem;background:#007bff;color:white;
              text-decoration:none;border-radius:4px;font-weight:500;">
      Download Instead
    </a>
  </div>
</body>
</html>"""
            return request.make_response(html, headers=[("Content-Type", "text/html")])

    # ─── Download ───────────────────────────────────────────────────────────────

    @http.route("/nextcloud/download/<int:file_id>", type="http", auth="user")
    def nextcloud_download(self, file_id, **kw):
        file_record = request.env["nextcloud.file"].sudo().browse(file_id)
        if not file_record.exists():
            return request.not_found()

        if not file_record.nextcloud_file_id:
            html = self._not_synced_html(file_record.name, file_id, action="download")
            return request.make_response(html, headers=[("Content-Type", "text/html")])

        try:
            content, content_type = self._get_s3_object(file_record)
            request.env["nextcloud.sync"].sudo()._log_file(
                file_record.drive_config_id, file_record, file_record.name, "download"
            )
            return request.make_response(content, headers=[
                ("Content-Type", content_type),
                ("Content-Disposition", http.content_disposition(file_record.name)),
            ])
        except Exception as exc:
            _logger.error("S3 download error for '%s': %s", file_record.name, exc)
            return f"Download error: {exc}"

    # ─── Preview ────────────────────────────────────────────────────────────────

    @http.route("/nextcloud/preview/<int:file_id>", type="http", auth="user")
    def nextcloud_preview(self, file_id, **kw):
        import os
        import urllib.parse
        import mimetypes

        file_record = request.env["nextcloud.file"].sudo().browse(file_id)
        if not file_record.exists():
            return request.not_found()

        if not file_record.nextcloud_file_id:
            html = self._not_synced_html(file_record.name, file_id, action="preview")
            return request.make_response(html, headers=[("Content-Type", "text/html")])

        _, ext = os.path.splitext(file_record.name.lower())

        # Office formats that browsers cannot render natively — use Google Docs Viewer
        office_exts = {
            '.doc', '.docx', '.docm',
            '.ppt', '.pps', '.ppsx', '.ppsm', '.pptx', '.pptm',
            '.xls', '.xlsx', '.xlsm',
            '.rtf', '.csv',
        }

        # All formats that browsers CAN render inline with a presigned URL
        # (PDF, images, plain text, SVG, HTML, video, audio, etc.)
        inline_exts = {
            '.pdf',
            '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.ico', '.tiff',
            '.txt', '.log', '.md', '.json', '.xml', '.yaml', '.yml', '.ini', '.cfg',
            '.html', '.htm', '.css', '.js',
            '.mp4', '.webm', '.ogv', '.ogg', '.mp3', '.wav',
        }

        try:
            config = file_record.drive_config_id
            sync_service = request.env["nextcloud.sync"].sudo()
            s3 = sync_service._get_s3_client(config)
            bucket = (config.aws_s3_bucket_name or "").strip()
            s3_key = (file_record.nextcloud_file_id or "").lstrip("/")

            if not s3_key:
                raise ValueError("No S3 key stored for this file.")

            guessed_mime, _ = mimetypes.guess_type(file_record.name)
            content_type = file_record.mime_type or guessed_mime or "application/octet-stream"

            # ── Office documents → Google Docs Viewer ──────────────────────────
            if ext in office_exts:
                presigned_url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket, "Key": s3_key},
                    ExpiresIn=3600,
                )
                viewer_url = (
                    "https://docs.google.com/viewer"
                    f"?url={urllib.parse.quote(presigned_url, safe='')}&embedded=true"
                )
                _logger.info("Office preview via Google Docs Viewer for '%s'", file_record.name)
                return werkzeug.utils.redirect(viewer_url, code=302)

            # ── PDF, images, text, video, audio → presigned inline redirect ───
            # The browser receives an S3 presigned URL with Content-Disposition:inline
            # so it renders the file natively (PDF viewer, image, media player, etc.)
            # instead of downloading. This also avoids X-Frame-Options / CSP issues
            # that blocked streaming through Odoo.
            if ext in inline_exts or content_type.startswith(('image/', 'video/', 'audio/', 'text/')):
                presigned_url = s3.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": bucket,
                        "Key": s3_key,
                        "ResponseContentType": content_type,
                        "ResponseContentDisposition": f'inline; filename="{file_record.name}"',
                    },
                    ExpiresIn=3600,
                )
                _logger.info("Inline preview presigned redirect for '%s' (%s)", file_record.name, content_type)
                return werkzeug.utils.redirect(presigned_url, code=302)

            # ── All other types → presigned download redirect ──────────────────
            # Archives, executables, etc.: browser will download natively via S3.
            presigned_url = s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket,
                    "Key": s3_key,
                    "ResponseContentDisposition": f'attachment; filename="{file_record.name}"',
                },
                ExpiresIn=3600,
            )
            _logger.info("Download presigned redirect for '%s'", file_record.name)
            return werkzeug.utils.redirect(presigned_url, code=302)

        except Exception as exc:
            _logger.error("S3 preview error for '%s': %s", file_record.name, exc)
            html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Preview Error</title></head>
<body style="font-family:sans-serif;display:flex;justify-content:center;
             align-items:center;height:100vh;margin:0;background:#f8f9fa;">
  <div style="text-align:center;padding:2rem;background:white;border-radius:8px;
              box-shadow:0 4px 6px rgba(0,0,0,.1);max-width:480px;">
    <h2 style="margin:0 0 1rem;color:#dc3545;">Preview Error</h2>
    <p style="color:#6c757d;margin-bottom:1.5rem;">{exc}</p>
    <a href="/nextcloud/download/{file_record.id}"
       style="display:inline-block;padding:.5rem 1rem;background:#007bff;color:white;
              text-decoration:none;border-radius:4px;font-weight:500;">
      Download Instead
    </a>
  </div>
</body>
</html>"""
            return request.make_response(html, headers=[("Content-Type", "text/html")])

    # ─── CSV Preview ────────────────────────────────────────────────────────────

    @http.route("/nextcloud/csv-preview/<int:file_id>", type="http", auth="user")
    def nextcloud_csv_preview(self, file_id, **kw):
        """Fetch CSV from S3 via Odoo and render as a styled HTML table.
        No external service required — works with private S3 buckets.
        """
        import csv
        import html as html_escape_mod

        file_record = request.env["nextcloud.file"].sudo().browse(file_id)
        if not file_record.exists():
            return request.not_found()

        if not file_record.nextcloud_file_id:
            return request.make_response(
                self._not_synced_html(file_record.name, file_id, action="preview"),
                headers=[("Content-Type", "text/html")]
            )

        MAX_ROWS = 500
        try:
            content, _ = self._get_s3_object(file_record)
            # Decode — try UTF-8 first, fall back to latin-1
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = content.decode("latin-1")

            reader = csv.reader(io.StringIO(text))
            rows = []
            for i, row in enumerate(reader):
                rows.append(row)
                if i >= MAX_ROWS:
                    break

            truncated = len(rows) > MAX_ROWS

            if not rows:
                table_html = "<p style='color:#888;'>Empty file.</p>"
            else:
                header = rows[0]
                data_rows = rows[1:]

                def esc(v):
                    return html_escape_mod.escape(str(v))

                th_cells = "".join(f"<th>{esc(h)}</th>" for h in header)
                tr_rows = "".join(
                    "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>"
                    for row in data_rows
                )
                table_html = f"""
                <table>
                  <thead><tr>{th_cells}</tr></thead>
                  <tbody>{tr_rows}</tbody>
                </table>
                """
                if truncated:
                    table_html += f"""
                    <p style="margin-top:12px;color:#888;font-size:0.85rem;">
                      ⚠ Showing first {MAX_ROWS} rows only.
                    </p>"""

            html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{html_escape_mod.escape(file_record.name)}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      font-size: 13px;
      background: #f8f9fa;
      color: #212529;
    }}
    .csv-header {{
      background: #fff;
      border-bottom: 1px solid #dee2e6;
      padding: 10px 16px;
      display: flex;
      align-items: center;
      gap: 8px;
      position: sticky;
      top: 0;
      z-index: 10;
    }}
    .csv-header span {{
      font-weight: 600;
      font-size: 14px;
      color: #343a40;
    }}
    .csv-header small {{
      color: #6c757d;
      font-size: 12px;
    }}
    .csv-wrap {{
      overflow: auto;
      padding: 12px;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      background: #fff;
      border-radius: 6px;
      overflow: hidden;
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
    }}
    thead tr {{
      background: #1a73e8;
      color: #fff;
    }}
    thead th {{
      padding: 8px 12px;
      text-align: left;
      font-weight: 600;
      white-space: nowrap;
      border-right: 1px solid rgba(255,255,255,.2);
    }}
    tbody tr:nth-child(even) {{ background: #f1f3f4; }}
    tbody tr:hover {{ background: #e8f0fe; }}
    td {{
      padding: 6px 12px;
      border-bottom: 1px solid #e9ecef;
      border-right: 1px solid #e9ecef;
      max-width: 300px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
  </style>
</head>
<body>
  <div class="csv-header">
    <span>📊 {html_escape_mod.escape(file_record.name)}</span>
    <small>({len(rows)} rows)</small>
  </div>
  <div class="csv-wrap">
    {table_html}
  </div>
</body>
</html>"""
            return request.make_response(html, headers=[("Content-Type", "text/html; charset=utf-8")])

        except Exception as exc:
            _logger.error("CSV preview error for '%s': %s", file_record.name, exc)
            return request.make_response(
                f"<h3>CSV Preview Error</h3><p>{exc}</p>",
                headers=[("Content-Type", "text/html")]
            )

    # ─── DOCX Preview ───────────────────────────────────────────────────────────

    @http.route("/nextcloud/docx-preview/<int:file_id>", type="http", auth="user")
    def nextcloud_docx_preview(self, file_id, **kw):
        """Render Word document (.doc/.docx) as HTML using python-docx."""
        import html as he
        try:
            from docx import Document
            from docx.oxml.ns import qn
        except ImportError:
            return request.make_response(
                "<h3>python-docx not installed. Run: pip install python-docx</h3>",
                headers=[("Content-Type", "text/html")]
            )

        file_record = request.env["nextcloud.file"].sudo().browse(file_id)
        if not file_record.exists() or not file_record.nextcloud_file_id:
            return request.not_found()

        try:
            content, _ = self._get_s3_object(file_record)
            doc = Document(io.BytesIO(content))

            body_parts = []
            for para in doc.paragraphs:
                text = para.text
                if not text.strip():
                    body_parts.append("<br>")
                    continue
                style = para.style.name if para.style else ""
                if style.startswith("Heading 1"):
                    body_parts.append(f"<h1>{he.escape(text)}</h1>")
                elif style.startswith("Heading 2"):
                    body_parts.append(f"<h2>{he.escape(text)}</h2>")
                elif style.startswith("Heading 3"):
                    body_parts.append(f"<h3>{he.escape(text)}</h3>")
                else:
                    # Inline bold/italic
                    runs_html = ""
                    for run in para.runs:
                        t = he.escape(run.text)
                        if run.bold and run.italic:
                            t = f"<strong><em>{t}</em></strong>"
                        elif run.bold:
                            t = f"<strong>{t}</strong>"
                        elif run.italic:
                            t = f"<em>{t}</em>"
                        runs_html += t
                    body_parts.append(f"<p>{runs_html}</p>")

            body_html = "\n".join(body_parts)

            html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>{he.escape(file_record.name)}</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; max-width: 860px; margin: 0 auto;
          padding: 32px 40px; background:#fff; color:#222; line-height:1.7; font-size:14px; }}
  h1,h2,h3 {{ color:#1a1a2e; margin: 1.2em 0 .4em; }}
  h1 {{ font-size:1.8em; border-bottom:2px solid #1a73e8; padding-bottom:6px; }}
  h2 {{ font-size:1.4em; }}
  h3 {{ font-size:1.15em; }}
  p {{ margin:.4em 0; }}
</style></head>
<body>
<div style="background:#f8f9fa;padding:10px 16px;margin-bottom:24px;border-radius:6px;
            display:flex;align-items:center;gap:8px;border:1px solid #dee2e6;">
  <span style="font-size:1.3rem;">📄</span>
  <strong style="font-size:14px;">{he.escape(file_record.name)}</strong>
</div>
{body_html}
</body></html>"""
            return request.make_response(html, headers=[("Content-Type", "text/html; charset=utf-8")])
        except Exception as exc:
            _logger.error("DOCX preview error '%s': %s", file_record.name, exc)
            return request.make_response(
                f"<h3>DOCX Preview Error</h3><p>{exc}</p>",
                headers=[("Content-Type", "text/html")]
            )

    # ─── XLSX Preview ───────────────────────────────────────────────────────────

    @http.route("/nextcloud/xlsx-preview/<int:file_id>", type="http", auth="user")
    def nextcloud_xlsx_preview(self, file_id, **kw):
        """Render Excel (.xls/.xlsx) sheets as styled HTML tables using openpyxl."""
        import html as he
        try:
            import openpyxl
        except ImportError:
            return request.make_response(
                "<h3>openpyxl not installed. Run: pip install openpyxl</h3>",
                headers=[("Content-Type", "text/html")]
            )

        file_record = request.env["nextcloud.file"].sudo().browse(file_id)
        if not file_record.exists() or not file_record.nextcloud_file_id:
            return request.not_found()

        MAX_ROWS = 500
        try:
            content, _ = self._get_s3_object(file_record)
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)

            sheets_html = ""
            tab_buttons = ""
            for idx, sheet_name in enumerate(wb.sheetnames):
                ws = wb[sheet_name]
                rows = []
                for row in ws.iter_rows(values_only=True):
                    rows.append(row)
                    if len(rows) > MAX_ROWS:
                        break

                if not rows:
                    table = "<p style='color:#888;padding:16px;'>Empty sheet.</p>"
                else:
                    header = rows[0]
                    th = "".join(f"<th>{he.escape(str(c) if c is not None else '')}</th>" for c in header)
                    trs = "".join(
                        "<tr>" + "".join(f"<td>{he.escape(str(c) if c is not None else '')}</td>" for c in row) + "</tr>"
                        for row in rows[1:]
                    )
                    note = f"<p style='color:#888;font-size:0.8rem;padding:8px 0;'>Showing first {MAX_ROWS} rows.</p>" if len(rows) > MAX_ROWS else ""
                    table = f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>{note}"

                display = "block" if idx == 0 else "none"
                active = "active" if idx == 0 else ""
                safe_name = he.escape(sheet_name)
                sheets_html += f'<div id="sheet_{idx}" class="sheet" style="display:{display}">{table}</div>'
                tab_buttons += f'<button class="tab {active}" onclick="showSheet({idx})">{safe_name}</button>'

            wb.close()

            html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>{he.escape(file_record.name)}</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          font-size:13px; background:#f8f9fa; color:#212529; }}
  .header {{ background:#fff; border-bottom:1px solid #dee2e6; padding:10px 16px;
             display:flex; align-items:center; gap:8px; position:sticky; top:0; z-index:10; }}
  .tabs {{ background:#fff; border-bottom:2px solid #1a73e8; padding:0 12px;
           display:flex; gap:4px; overflow-x:auto; }}
  .tab {{ padding:8px 16px; border:none; background:none; cursor:pointer;
          font-size:13px; color:#555; border-bottom:3px solid transparent; margin-bottom:-2px; }}
  .tab.active {{ color:#1a73e8; border-bottom-color:#1a73e8; font-weight:600; }}
  .sheet {{ overflow:auto; padding:12px; }}
  table {{ border-collapse:collapse; background:#fff; width:100%;
           box-shadow:0 1px 4px rgba(0,0,0,.08); }}
  thead tr {{ background:#1a73e8; color:#fff; }}
  thead th {{ padding:8px 12px; text-align:left; font-weight:600;
              white-space:nowrap; border-right:1px solid rgba(255,255,255,.2); }}
  tbody tr:nth-child(even) {{ background:#f1f3f4; }}
  tbody tr:hover {{ background:#e8f0fe; }}
  td {{ padding:6px 12px; border-bottom:1px solid #e9ecef;
        border-right:1px solid #e9ecef; max-width:280px;
        overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
</style></head>
<body>
<div class="header">
  <span style="font-size:1.3rem;">📊</span>
  <strong>{he.escape(file_record.name)}</strong>
</div>
<div class="tabs">{tab_buttons}</div>
{sheets_html}
<script>
function showSheet(idx) {{
  document.querySelectorAll('.sheet').forEach(s => s.style.display='none');
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('sheet_'+idx).style.display='block';
  document.querySelectorAll('.tab')[idx].classList.add('active');
}}
</script>
</body></html>"""
            return request.make_response(html, headers=[("Content-Type", "text/html; charset=utf-8")])
        except Exception as exc:
            _logger.error("XLSX preview error '%s': %s", file_record.name, exc)
            return request.make_response(
                f"<h3>XLSX Preview Error</h3><p>{exc}</p>",
                headers=[("Content-Type", "text/html")]
            )

    # ─── PPTX Preview ───────────────────────────────────────────────────────────

    @http.route("/nextcloud/pptx-preview/<int:file_id>", type="http", auth="user")
    def nextcloud_pptx_preview(self, file_id, **kw):
        """Render PowerPoint (.ppt/.pptx) slides as styled HTML cards using python-pptx."""
        import html as he
        try:
            from pptx import Presentation
        except ImportError:
            return request.make_response(
                "<h3>python-pptx not installed. Run: pip install python-pptx</h3>",
                headers=[("Content-Type", "text/html")]
            )

        file_record = request.env["nextcloud.file"].sudo().browse(file_id)
        if not file_record.exists() or not file_record.nextcloud_file_id:
            return request.not_found()

        try:
            content, _ = self._get_s3_object(file_record)
            prs = Presentation(io.BytesIO(content))

            slides_html = ""
            for i, slide in enumerate(prs.slides, 1):
                texts = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            line = para.text.strip()
                            if line:
                                texts.append(he.escape(line))

                title_text = texts[0] if texts else f"Slide {i}"
                body_texts = texts[1:]
                body_html = "".join(f"<li>{t}</li>" for t in body_texts)
                body_section = f"<ul>{body_html}</ul>" if body_html else ""

                slides_html += f"""
<div class="slide">
  <div class="slide-num">Slide {i}</div>
  <div class="slide-title">{title_text}</div>
  {body_section}
</div>"""

            html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>{he.escape(file_record.name)}</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          background:#f0f2f5; padding:16px; }}
  .header {{ background:#fff; border-radius:8px; padding:12px 16px; margin-bottom:16px;
             display:flex; align-items:center; gap:8px; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
  .slide {{ background:#fff; border-radius:8px; padding:32px 40px; margin-bottom:16px;
            box-shadow:0 2px 8px rgba(0,0,0,.08); min-height:140px; position:relative; }}
  .slide-num {{ position:absolute; top:12px; right:16px; font-size:11px;
                color:#888; background:#f1f3f4; padding:2px 8px; border-radius:10px; }}
  .slide-title {{ font-size:1.4rem; font-weight:700; color:#1a1a2e; margin-bottom:12px; }}
  ul {{ padding-left:20px; color:#444; line-height:1.8; }}
  li {{ margin-bottom:4px; }}
</style></head>
<body>
<div class="header">
  <span style="font-size:1.3rem;">📊</span>
  <strong>{he.escape(file_record.name)}</strong>
  <span style="color:#888;font-size:12px;">({len(prs.slides)} slides)</span>
</div>
{slides_html}
</body></html>"""
            return request.make_response(html, headers=[("Content-Type", "text/html; charset=utf-8")])
        except Exception as exc:
            _logger.error("PPTX preview error '%s': %s", file_record.name, exc)
            return request.make_response(
                f"<h3>PPTX Preview Error</h3><p>{exc}</p>",
                headers=[("Content-Type", "text/html")]
            )

    # ─── ZIP Download ───────────────────────────────────────────────────────────


    @http.route("/nextcloud/download_zip", type="http", auth="user")
    def nextcloud_download_zip(self, file_ids, **kw):
        if not file_ids:
            return "No files selected."
        try:
            ids = [int(i) for i in file_ids.split(",")]
            file_model = request.env["nextcloud.file"]
            all_files = file_model.sudo().get_recursive_files_for_zip(ids)
            if not all_files:
                return "No files found to download."

            sync_service = request.env["nextcloud.sync"].sudo()
            first_rec = all_files[0][0]
            config = first_rec.drive_config_id
            s3 = sync_service._get_s3_client(config)
            bucket = (config.aws_s3_bucket_name or "").strip()

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for record, rel_path in all_files:
                    s3_key = (record.nextcloud_file_id or "").lstrip("/")
                    if not s3_key:
                        _logger.warning(
                            "ZIP: skipping '%s' — no S3 key (file not synced)", rel_path
                        )
                        continue
                    try:
                        resp = s3.get_object(Bucket=bucket, Key=s3_key)
                        zf.writestr(rel_path, resp["Body"].read())
                    except Exception as loop_exc:
                        _logger.warning(
                            "ZIP: error fetching '%s' from S3: %s", rel_path, loop_exc
                        )

            zip_buffer.seek(0)
            zip_filename = "aws_s3_export.zip"
            log_name = "Bulk Download (ZIP)"
            if len(ids) == 1:
                rec = file_model.sudo().browse(ids[0])
                zip_filename = f"{rec.name}.zip"
                log_name = f"Download ZIP: {rec.name}"

            sync_service._log(config, log_name, "download")
            return request.make_response(
                zip_buffer.getvalue(),
                headers=[
                    ("Content-Type", "application/zip"),
                    ("Content-Disposition", http.content_disposition(zip_filename)),
                ],
            )
        except Exception as exc:
            return f"ZIP Download Error: {exc}"
