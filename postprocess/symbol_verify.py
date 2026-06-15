"""利用多模态 LLM (qwen-vl-plus) 复核 OCR 识别出的符号定义行。

建设工程规范的符号定义行（如"2.2 符号"章节）典型格式：
    $f_y$ —— 钢材的屈服强度；
    $h_0$ —— 构件加固后的截面有效高度；

OCR 常见错误：
    - 公式下标最后一个字符在关 $ 后被重复为一个独立字母/数字
      例：$f_y$ y-钢材的屈服强度  →  $f_y$ —— 钢材的屈服强度
           $h_{01}$ 1-构件加固后  →  $h_{01}$ —— 构件加固后
    - 中文破折号 —— 被识别为单个 - 或 —
      例：$E_a$  -新增型钢弹性模量  →  $E_a$ —— 新增型钢弹性模量

用法：
    - 默认 dry-run：只扫描生成清单，不动正文
    - verify_symbols_with_vlm() 把 PDF 页渲染为 PNG，连带完整行发给 VLM 校对

依赖：fitz (PyMuPDF)、openai SDK。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path

PAGE_RE = re.compile(r"^<!--\s*(\d+)\s*-->$")

# 包含行内公式且公式后跟短横线 + 中文的行（排除已有规范破折号 —— 的行）
# 匹配：$f_y$ y-新增...、$h_0$  -截面...、$E_a$ — 弹性模量
# 不匹配：$V_{Eki}$——第 $i$ 层...（双破折号正确）
_INLINE_FORMULA_RE = re.compile(r"\$[^$\n]{1,120}\$")

# 正确格式：公式后直接跟 —— 再接中文（说明符号定义规范）
_PROPER_SYMBOL_RE = re.compile(
    r"\$[^$\n]{1,120}\$\s*——\s*[一-鿿（(]"
)

# 可疑格式：公式后跟短横线/破折号 + 中文
# P0：公式后有重复字符 + 短横线 + 中文
_SYMBOL_DUP_RE = re.compile(
    r"\$[^$\n]{1,120}\$\s*[A-Za-z0-9]+\s*[-—–―－]\s*[一-鿿（(]"
)
# P1：公式后有空格 + 单短横 + 中文（不含双破折号）
_SYMBOL_SINGLE_DASH_RE = re.compile(
    r"\$[^$\n]{1,120}\$\s+[-—–―－](?![-—–―－])\s*[一-鿿（(]"
)


@dataclass
class SymbolDefHit:
    """符号定义行命中记录。"""

    line_text: str          # 完整行文本
    page: int | None        # 所在 PDF 页号
    line_index: int         # 所在 md 行号
    suggested_line: str | None = None  # VLM 修正后的完整行


def _is_symbol_line(line: str) -> bool:
    """判断一行是否属于符号定义行（疑似 OCR 错误）。

    判定条件：
    1. 包含至少一个行内公式 $...$
    2. 公式后跟短横线 + 中文（但非规范破折号 ——）
    3. 不是标题、表格、代码块、注释行
    """
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("#", "<!--", "$$", "```", "|---", "|")):
        return False
    if "<table" in stripped or "</table" in stripped or "<tr" in stripped:
        return False

    # 至少有一个行内公式
    if not _INLINE_FORMULA_RE.search(stripped):
        return False

    # 如果已经是规范的 —— 格式，跳过
    if _PROPER_SYMBOL_RE.search(stripped):
        # 但仍需检查是否有其他公式后跟错误后缀（一行可能有多个公式）
        # 简单策略：如果整行都是规范格式，跳过
        all_proper = True
        for m in _INLINE_FORMULA_RE.finditer(stripped):
            after = stripped[m.end():]
            if not re.match(r"^\s*——", after):
                # 检查这个公式后面是否为错误后缀
                if _SYMBOL_DUP_RE.match(stripped[m.start():]):
                    all_proper = False
                    break
        if all_proper:
            return False

    # P0：公式后有重复字符 + 短横线
    if _SYMBOL_DUP_RE.search(stripped):
        return True

    # P1：公式后单短横 + 中文
    if _SYMBOL_SINGLE_DASH_RE.search(stripped):
        return True

    return False


def scan_symbol_defs(text: str) -> list[SymbolDefHit]:
    """扫描全文，找出所有疑似符号定义 OCR 错误的行。

    返回按 line_index 排序的命中列表。
    """
    hits: list[SymbolDefHit] = []
    lines = text.splitlines()
    current_page: int | None = None
    in_table = False

    for idx, line in enumerate(lines):
        stripped = line.strip()

        # 追踪页码
        page_m = PAGE_RE.match(stripped)
        if page_m:
            current_page = int(page_m.group(1))
            in_table = False
            continue

        # 追踪表格边界（表格内的行不检测）
        if "<table" in stripped:
            in_table = True
            continue
        if "</table" in stripped:
            in_table = False
            continue
        if in_table:
            continue

        if _is_symbol_line(line):
            hits.append(
                SymbolDefHit(
                    line_text=line,
                    page=current_page,
                    line_index=idx,
                )
            )

    return hits


def write_symbol_report(text: str, out_path: Path) -> int:
    """扫描符号定义错误并写为 markdown 报告文件。

    返回扫描到的可疑符号定义行数量。
    """
    hits = scan_symbol_defs(text)
    lines = ["# 符号定义 OCR 复核清单", ""]
    lines.append(f"共 {len(hits)} 处疑似符号定义错误。")
    lines.append("")
    lines.append("对照 PDF 对应页号复核下列行的 OCR 识别结果：")
    lines.append("")

    for i, h in enumerate(hits, 1):
        lines.append(f"## 符号 #{i}（PDF 页 {h.page or '?'}，md 行 {h.line_index + 1}）")
        lines.append("")
        # 截取行中的公式部分展示
        formulas = _INLINE_FORMULA_RE.findall(h.line_text.strip())
        if formulas:
            lines.append(f"公式: {' '.join(formulas)}")
        lines.append("")
        lines.append("```")
        lines.append(h.line_text.strip())
        lines.append("```")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return len(hits)


def _render_page_png(pdf_path: Path, page_no: int) -> bytes | None:
    """渲染 PDF 的指定页为 PNG，返回图片字节。

    页号越界（印刷页码大于 PDF 实际物理页数）时返回 None，由调用方跳过，
    避免单条命中导致整条流水线崩溃。
    """
    import fitz

    doc = fitz.open(pdf_path)
    if page_no < 1 or page_no > doc.page_count:
        return None
    page = doc[page_no - 1]
    pix = page.get_pixmap(dpi=200)
    return pix.tobytes("png")


def verify_symbols_with_vlm(
    text: str,
    pdf_path: Path,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
    on_progress=None,
    checkpoint=None,
) -> tuple[str, list[SymbolDefHit]]:
    """对每个疑似符号定义错误行调用 VLM 校核，返回 (修正后全文, 命中列表).

    Parameters:
        on_progress(current, total): 每处理一个符号时回调一次。
        checkpoint: Checkpoint 实例，支持断点续跑。
        dry_run: True 时不调用 API，只返回原文 + 命中列表。

    Returns:
        (修正后的全文文本, 命中列表（含 VLM 建议修改）)
    """
    from .config import get_openai_client, create_chat_completion, FELIZ_MODEL

    hits = scan_symbol_defs(text)
    total = len(hits)

    if dry_run or not pdf_path.exists():
        return text, hits

    client = get_openai_client(base_url=base_url, api_key=api_key)
    model_name = model or FELIZ_MODEL
    ck_step = "symbol_verify"
    skipped = 0
    modified = 0

    lines = text.splitlines()
    corrected_lines: dict[int, str] = {}  # line_index → corrected text

    for idx, h in enumerate(hits, 1):
        if on_progress:
            on_progress(idx, total)

        if h.page is None:
            continue

        key = f"{h.page}:{h.line_index}"
        if checkpoint and checkpoint.is_done(ck_step, key):
            cached = checkpoint.get_result(ck_step, key)
            if cached and cached != h.line_text:
                h.suggested_line = cached
                corrected_lines[h.line_index] = cached
            skipped += 1
            continue

        png = _render_page_png(pdf_path, h.page)
        if png is None:
            print(f"  [symbol] 跳过越界页码 {h.page}（PDF 实际页数不足，保留原文）")
            if checkpoint:
                checkpoint.mark_done(ck_step, key, result=h.line_text)
            skipped += 1
            continue
        b64 = base64.b64encode(png).decode()

        # 构造上下文：向 VLM 发送 PDF 截图 + OCR 行，让其修正
        strip_line = h.line_text.strip()
        prompt = (
            "你是一位工程规范文档校对专家。下面是PDF页面截图，其中包含符号定义行。\n\n"
            "OCR识别出的符号定义文本可能存在以下错误：\n"
            "- 公式（$...$）后面的字母/数字是下标最后一个字符被错误重复\n"
            "- 短横线'-'或'—'应为中文破折号'——'\n\n"
            f"OCR识别的行：\n{strip_line}\n\n"
            "请对照PDF截图中的实际内容，返回修正后的完整行。\n"
            "规则：\n"
            "1. 保持行内公式 $...$ 不变（公式本身由另一个流程复核）\n"
            "2. 删除公式后面多余的重复字符（OCR噪声）\n"
            "3. 如果公式后缺少或使用错误的破折号，统一改为中文破折号'——'\n"
            "4. 只返回修正后的行，不要添加任何解释或标记\n"
            "5. 如果原行已正确，原样返回。"
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
        if corrected and corrected != strip_line:
            h.suggested_line = corrected
            corrected_lines[h.line_index] = corrected
            modified += 1

        if checkpoint:
            checkpoint.mark_done(ck_step, key, result=corrected or strip_line)

    # 回填修正后的行
    if corrected_lines:
        result_lines = []
        for i, line in enumerate(lines):
            if i in corrected_lines:
                result_lines.append(corrected_lines[i])
            else:
                result_lines.append(line)
        new_text = "\n".join(result_lines)
    else:
        new_text = text

    # 更新报告
    if modified > 0:
        print(f"\n  [symbol] 已修正 {modified} 行符号定义错误")
    if skipped > 0:
        print(f"  [symbol] 从断点跳过 {skipped} 条已处理符号")

    return new_text, hits
