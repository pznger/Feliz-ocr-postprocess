"""利用多模态 LLM 复核 OCR 出来的 HTML 表格。

- 默认 dry-run：只扫描所有 <table> 生成复核清单，不动正文。
- verify_with_vlm() 把 PDF 页面截图 + OCR HTML 发给 VLM，收到修正版后回填。

依赖：fitz (PyMuPDF)、openai SDK。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

PAGE_RE = re.compile(r"^<!--\s*(\d+)\s*-->$")
TABLE_OPEN_RE = re.compile(r"<table\b", re.IGNORECASE)
TABLE_CLOSE_RE = re.compile(r"</table>", re.IGNORECASE)


@dataclass
class TableHit:
    raw_html: str
    page: int | None
    line_index: int
    suggested: str | None = None


def _scan_tables(text: str) -> list[TableHit]:
    hits: list[TableHit] = []
    lines = text.splitlines()
    current_page: int | None = None
    i = 0
    while i < len(lines):
        page_m = PAGE_RE.match(lines[i].strip())
        if page_m:
            current_page = int(page_m.group(1))
            i += 1
            continue
        if TABLE_OPEN_RE.search(lines[i]):
            start = i
            buf = [lines[i]]
            while i + 1 < len(lines) and not TABLE_CLOSE_RE.search(lines[i]):
                i += 1
                buf.append(lines[i])
            hits.append(TableHit(raw_html="\n".join(buf), page=current_page, line_index=start))
        i += 1
    return hits


def write_table_report(text: str, out_path: Path) -> int:
    hits = _scan_tables(text)
    lines = ["# 表格 OCR 复核清单", ""]
    lines.append(f"共 {len(hits)} 张表格。对照 PDF 对应页号复核结构与数值：")
    lines.append("")
    for i, h in enumerate(hits, 1):
        lines.append(f"## 表格 #{i}（PDF 页 {h.page or '?'}，md 行 {h.line_index + 1}）")
        lines.append("")
        lines.append("```html")
        lines.append(h.raw_html)
        lines.append("```")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return len(hits)


def verify_with_vlm(
    text: str,
    pdf_path: Path,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
    on_progress=None,
    checkpoint=None,
) -> tuple[str, list[TableHit]]:
    hits = _scan_tables(text)
    total = len(hits)
    if dry_run or not pdf_path.exists():
        return text, hits

    from .config import get_openai_client, create_chat_completion, FELIZ_MODEL

    import fitz

    client = get_openai_client(base_url=base_url, api_key=api_key)
    model_name = model or FELIZ_MODEL
    doc = fitz.open(pdf_path)
    ck_step = "table_verify"
    new_text = text
    for idx, h in enumerate(hits, 1):
        if on_progress:
            on_progress(idx, total)
        if h.page is None:
            continue
        key = f"{h.page}:{h.line_index}"
        if checkpoint and checkpoint.is_done(ck_step, key):
            corrected = checkpoint.get_result(ck_step, key)
            if corrected and corrected.startswith("<table") and corrected != h.raw_html:
                h.suggested = corrected
                new_text = new_text.replace(h.raw_html, corrected, 1)
            continue
        if h.page < 1 or h.page > doc.page_count:
            print(f"  [table] 跳过越界页码 {h.page}（PDF 实际页数不足，保留原文）")
            if checkpoint:
                checkpoint.mark_done(ck_step, key, result=h.raw_html)
            continue
        pix = doc[h.page - 1].get_pixmap(dpi=180)
        b64 = base64.b64encode(pix.tobytes("png")).decode()
        prompt = (
            "下面是 PDF 一页的截图，以及从该页 OCR 出来的 HTML 表格。"
            "请直接返回修正后的 `<table>...</table>` 整段 HTML（不要解释）。"
            "如果原始表格已经正确，原样返回。\n\nOCR 表格：\n" + h.raw_html
        )
        rsp = create_chat_completion(
            client,
            model=model_name,
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        corrected = rsp.choices[0].message.content.strip() if rsp.choices else ""
        if corrected.startswith("<table") and corrected != h.raw_html:
            h.suggested = corrected
            new_text = new_text.replace(h.raw_html, corrected, 1)
        if checkpoint:
            checkpoint.mark_done(ck_step, key, result=corrected or h.raw_html)
    return new_text, hits
