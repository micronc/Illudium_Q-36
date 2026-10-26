"""Export analyst reports to Markdown, PDF, or DOCX."""

from __future__ import annotations

import datetime
import io
import re
from typing import Any


def _strip_mermaid_for_plain(text: str) -> str:
    """Keep mermaid source visible as fenced code in non-HTML exports."""
    return text or ""


def build_report_markdown(
    *,
    title: str,
    body: str,
    model: str | None = None,
    usage: dict[str, Any] | None = None,
    atlas_note: str | None = None,
) -> str:
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    parts = [
        f"# {title}",
        f"**Generated:** {stamp}",
    ]
    if model:
        parts.append(f"**Model:** `{model}`")
    if usage:
        parts.append(
            f"**Tokens:** {usage.get('total_tokens', 0)} "
            f"(in {usage.get('input_tokens', 0)} / out {usage.get('output_tokens', 0)}) · "
            f"**Est. cost:** ${float(usage.get('cost_usd', 0)):.4f} USD"
        )
    if atlas_note:
        parts.append(f"**ATLAS note:** {atlas_note}")
    parts.extend(["", "---", "", _strip_mermaid_for_plain(body).strip(), ""])
    return "\n".join(parts)


def export_markdown(md: str) -> bytes:
    return md.encode("utf-8")


def export_docx(md: str) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    in_code = False
    code_lines: list[str] = []

    def flush_code():
        nonlocal code_lines
        if not code_lines:
            return
        p = doc.add_paragraph()
        run = p.add_run("\n".join(code_lines))
        run.font.name = "Consolas"
        run.font.size = Pt(9)
        code_lines = []

    for line in md.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if line.startswith("# "):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=3)
        elif line.startswith("- "):
            doc.add_paragraph(line[2:].strip(), style="List Bullet")
        elif re.match(r"^\d+\.\s+", line):
            doc.add_paragraph(re.sub(r"^\d+\.\s+", "", line), style="List Number")
        elif line.strip() == "---":
            doc.add_paragraph("—" * 20)
        elif line.strip():
            # light markdown bold
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
            text = re.sub(r"`([^`]+)`", r"\1", text)
            doc.add_paragraph(text)
        else:
            doc.add_paragraph("")

    flush_code()
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def export_pdf(md: str) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(name="BodyIlludium", parent=styles["BodyText"], fontSize=10, leading=14)
    )
    styles.add(
        ParagraphStyle(name="H1Illudium", parent=styles["Heading1"], fontSize=16, spaceAfter=8)
    )
    styles.add(
        ParagraphStyle(name="H2Illudium", parent=styles["Heading2"], fontSize=13, spaceAfter=6)
    )
    styles.add(
        ParagraphStyle(
            name="CodeIlludium",
            parent=styles["Code"],
            fontSize=8,
            leading=10,
            fontName="Courier",
        )
    )

    story = []
    in_code = False
    code_lines: list[str] = []

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def flush_code():
        nonlocal code_lines
        if code_lines:
            story.append(Preformatted("\n".join(code_lines), styles["CodeIlludium"]))
            story.append(Spacer(1, 8))
            code_lines = []

    for line in md.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        raw = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        raw = re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", raw)
        if line.startswith("# "):
            story.append(Paragraph(esc(line[2:].strip()), styles["H1Illudium"]))
        elif line.startswith("## "):
            story.append(Paragraph(esc(line[3:].strip()), styles["H2Illudium"]))
        elif line.startswith("### "):
            story.append(Paragraph(esc(line[4:].strip()), styles["Heading3"]))
        elif line.strip():
            story.append(Paragraph(raw if "<" in raw else esc(line), styles["BodyIlludium"]))
            story.append(Spacer(1, 4))
        else:
            story.append(Spacer(1, 6))

    flush_code()
    doc.build(story)
    return buf.getvalue()


def export_report(md: str, fmt: str) -> tuple[bytes, str, str]:
    """
    Returns (bytes, mimetype, filename_suffix).
    fmt: md | pdf | docx
    """
    fmt = (fmt or "md").lower().strip(".")
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    # Use attachment-friendly MIME types so browsers download instead of navigating.
    if fmt == "pdf":
        return export_pdf(md), "application/pdf", f"illudium_report_{stamp}.pdf"
    if fmt in {"docx", "doc"}:
        return (
            export_docx(md),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            f"illudium_report_{stamp}.docx",
        )
    return (
        export_markdown(md),
        "application/octet-stream",
        f"illudium_report_{stamp}.md",
    )
