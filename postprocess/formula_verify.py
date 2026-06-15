"""利用多模态 LLM (qwen3.6-plus) 复核 OCR 出来的公式。

- 默认 dry-run：只扫描所有 $$...$$ 生成复核清单，不动正文。
- 显式调用 verify_with_vlm() 时把 PDF 页渲染为 PNG，连同 OCR LaTeX 发给 VLM 校
  对，收到修正版后回填。

依赖：fitz (PyMuPDF)、openai SDK。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

PAGE_RE = re.compile(r"^<!--\s*(\d+)\s*-->$")
FORMULA_BLOCK_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)


@dataclass
class FormulaHit:
    raw: str          # $$...$$ 内的 LaTeX 原文
    page: int | None  # 所在 PDF 页号（按最近的 <!-- N --> 推断）
    line_index: int   # 所在 md 行号
    suggested: str | None = None  # VLM 修正版本


@dataclass
class FormulaChange:
    """公式修改记录。"""
    original: str       # 原始 OCR LaTeX
    corrected: str      # VLM 修正后的 LaTeX
    page: int | None
    line_index: int


def _scan_formulas(text: str) -> list[FormulaHit]:
    hits: list[FormulaHit] = []
    current_page: int | None = None
    lines = text.splitlines()
    in_formula = False
    formula_start_line = 0
    for idx, line in enumerate(lines):
        page_m = PAGE_RE.match(line.strip())
        if page_m:
            current_page = int(page_m.group(1))
            continue
        # 单行公式
        if line.strip().startswith("$$") and line.strip().endswith("$$") and len(line.strip()) > 4:
            content = line.strip().strip("$").strip()
            hits.append(FormulaHit(raw=content, page=current_page, line_index=idx))
            continue
        if "$$" in line and not in_formula:
            in_formula = True
            formula_start_line = idx
            continue
        if in_formula:
            if "$$" in line:
                in_formula = False
                # 多行公式块：重新扫描整段
                block_lines = lines[formula_start_line : idx + 1]
                block = "\n".join(block_lines)
                m = FORMULA_BLOCK_RE.search(block)
                if m:
                    hits.append(
                        FormulaHit(
                            raw=m.group(1).strip(),
                            page=current_page,
                            line_index=formula_start_line,
                        )
                    )
    return hits


def write_formula_report(
    text: str,
    out_path: Path,
    changes: list[FormulaChange] | None = None,
) -> int:
    """把全部公式块 dump 成 markdown 复核清单。

    如果提供了 changes 列表，会在报告中标出已修正的公式并展示前后对比。
    """
    hits = _scan_formulas(text)
    # 构建行号到变化的映射，便于快速查找
    change_map: dict[int, FormulaChange] = {}
    if changes:
        for c in changes:
            change_map[c.line_index] = c

    lines = ["# 公式 OCR 复核清单", ""]
    lines.append(f"共 {len(hits)} 处公式。")
    if changes:
        lines.append(f"其中 {len(changes)} 处已被 VLM 修正。")
    lines.append("")
    lines.append("对照 PDF 对应页号复核：")
    lines.append("")

    for i, h in enumerate(hits, 1):
        ch = change_map.get(h.line_index)
        if ch:
            lines.append(
                f"## 公式 #{i}（PDF 页 {h.page or '?'}，md 行 {h.line_index + 1}）⚠️ 已修正"
            )
        else:
            lines.append(
                f"## 公式 #{i}（PDF 页 {h.page or '?'}，md 行 {h.line_index + 1}）"
            )
        lines.append("")
        if ch:
            lines.append(f"**原始**：")
            lines.append("```latex")
            lines.append(ch.original)
            lines.append("```")
            lines.append("")
            lines.append(f"**修正**：")
            lines.append("```latex")
            lines.append(ch.corrected)
            lines.append("```")
        else:
            lines.append("```latex")
            lines.append(h.raw)
            lines.append("```")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return len(hits)


def _render_page_png(pdf_path: Path, page_no: int) -> bytes | None:
    """渲染 PDF 指定页为 PNG；页号越界时返回 None，由调用方跳过。"""
    import fitz

    doc = fitz.open(pdf_path)
    if page_no < 1 or page_no > doc.page_count:
        return None
    page = doc[page_no - 1]
    pix = page.get_pixmap(dpi=180)
    return pix.tobytes("png")


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
    changes: list[FormulaChange] | None = None,
) -> tuple[str, list[FormulaHit]]:
    """对每个公式块调 VLM 复核，返回 (修正后文本, 命中列表)。

    dry_run=True 时不调用 API，只返回原文 + 命中列表。
    on_progress(current, total) 每复核一个公式调用一次。
    checkpoint: Checkpoint 实例，支持断点续跑——已复核的公式跳过 API 调用直接用缓存结果。
    changes: 如果提供列表，每次修改都会追加 FormulaChange 记录（用于后续生成修改报告）。
    """
    from .config import get_openai_client, create_chat_completion, FELIZ_MODEL

    hits = _scan_formulas(text)
    total = len(hits)
    if dry_run or not pdf_path.exists():
        return text, hits

    client = get_openai_client(base_url=base_url, api_key=api_key)
    model_name = model or FELIZ_MODEL
    ck_step = "formula_verify"
    skipped = 0

    new_text = text
    for idx, h in enumerate(hits, 1):
        if on_progress:
            on_progress(idx, total)
        if h.page is None:
            continue
        key = f"{h.page}:{h.line_index}"
        if checkpoint and checkpoint.is_done(ck_step, key):
            corrected = checkpoint.get_result(ck_step, key)
            if corrected and corrected != h.raw:
                h.suggested = corrected
                new_text = new_text.replace(f"$${h.raw}$$", f"$${corrected}$$", 1)
                if changes is not None:
                    changes.append(FormulaChange(
                        original=h.raw, corrected=corrected,
                        page=h.page, line_index=h.line_index,
                    ))
            skipped += 1
            continue
        png = _render_page_png(pdf_path, h.page)
        if png is None:
            print(f"  [formula] 跳过越界页码 {h.page}（PDF 实际页数不足，保留原文）")
            if checkpoint:
                checkpoint.mark_done(ck_step, key, result=h.raw)
            skipped += 1
            continue
        b64 = base64.b64encode(png).decode()
        prompt = (
            "下面是一张工程规范的 PDF 页面截图，以及一段从该页面 OCR 得到的 LaTeX 公式。"
            "请只返回修正后的 LaTeX 公式本体（不要加 `$$`、不要加任何说明）。"
            "如果原 LaTeX 已经正确，原样返回。\n\nOCR 内容：\n" + h.raw
        )
        rsp = create_chat_completion(
            client,
            model=model_name,
            max_tokens=400,
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
        if corrected and corrected != h.raw:
            h.suggested = corrected
            new_text = new_text.replace(f"$${h.raw}$$", f"$${corrected}$$", 1)
            if changes is not None:
                changes.append(FormulaChange(
                    original=h.raw, corrected=corrected,
                    page=h.page, line_index=h.line_index,
                ))
        if checkpoint:
            checkpoint.mark_done(ck_step, key, result=corrected or h.raw)
    return new_text, hits
