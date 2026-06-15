"""基于 LLM 的段落级 OCR 文本修复。

场景：规则化的 text_normalize.py 修完常见噪声之后，仍有一些段落包含
规则难以覆盖的 OCR 乱码（错误拆分/合并的中文词、字形相近的字符替换、
上下文相关的断句错误等）。

策略：
- 扫描全文段落，用一组启发式信号（中文内嵌乱码字、不成词单字串、异常
  CJK-ASCII 混排比率）标记疑似受损段落。
- 把每个受损段落 + 紧邻 1 个上下文段落一起发给 LLM 修复。
- LLM 只做"最小修正"：不允许重写/删减内容，只改正 OCR 带来的字词错误。

依赖：openai SDK。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ── 启发式规则：判定一段落是否"像被 OCR 搞坏了" ──────────

# CJK 范围内极少出现的字符（OCR 容易误识的区域）
_RARE_CJK_KILLER = re.compile(
    r"[扌牜艹㇑Ｊ脒緂籥䁖䁖辐莳艏龘]"  # 示例集合；每次遇到新规可扩充
)

# 一行中 ASCII 占比过高且混杂中文
_ASCII_CJK_MIX = re.compile(
    r"[\x00-\x7f]{6,}[一-鿿]{2,}|[一-鿿]{2,}[\x00-\x7f]{6,}"
)

# 条款编号内部出现异常空格（比如原本的编号被分段）
_BROKEN_CLAUSE_LINE = re.compile(r"\d+\.\d+\.\d+[^\d]{0,3}$")

# 被 OCR 拆散的单字（连续 >3 个单字中间夹空格）——很可能是表格/标题被打散
_SINGLE_CHAR_FRAG = re.compile(r"(?:[一-鿿]\s){3,}[一-鿿]")


def paragraph_suspicious(para: str) -> bool:
    """返回 True 表示这个段落可能需要 LLM 修复。"""
    if len(para) < 12:
        return False
    # 公式块 / 表格直接跳过
    if para.strip().startswith(("$$", "<!--", "<table", "<tr", "<td")):
        return False
    if para.strip().startswith("#"):
        return False
    if _RARE_CJK_KILLER.search(para):
        return True
    if _ASCII_CJK_MIX.search(para):
        return True
    if _SINGLE_CHAR_FRAG.search(para):
        return True
    return False


def split_paragraphs(text: str) -> list[tuple[int, str]]:
    """按空行切段，返回 (段首行号, 段落文本)。"""
    lines = text.splitlines()
    paras: list[tuple[int, str]] = []
    buf: list[str] = []
    start_line = 0
    for i, line in enumerate(lines):
        if line.strip() == "":
            if buf:
                paras.append((start_line, "\n".join(buf)))
                buf = []
            continue
        if not buf:
            start_line = i
        buf.append(line)
    if buf:
        paras.append((start_line, "\n".join(buf)))
    return paras


def clean_with_llm(
    text: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
    on_progress=None,
    checkpoint=None,
) -> tuple[str, int]:
    """对疑似受损段落逐段调 LLM 修复。

    返回 (修复后全文, 修复段落数)。
    dry_run=True 时不调 API，只统计可疑段落数。
    on_progress(current, total, repaired_so_far) 每处理一个段落调用一次。
    checkpoint: Checkpoint 实例，支持断点续跑。
    """
    paras = split_paragraphs(text)
    suspicious_indices = [j for j, (_, p) in enumerate(paras) if paragraph_suspicious(p)]

    if dry_run or not suspicious_indices:
        return text, len(suspicious_indices)

    from .config import get_openai_client, create_chat_completion, FELIZ_MODEL

    client = get_openai_client(base_url=base_url, api_key=api_key)
    model_name = model or FELIZ_MODEL
    repaired = 0
    total = len(suspicious_indices)
    ck_step = "text_clean_llm"

    # 需要按原顺序重建文本；从后往前替换避免索引偏移
    for step, j in enumerate(reversed(suspicious_indices), 1):
        if on_progress:
            on_progress(step, total, repaired)
        key = str(paras[j][0])  # 段落起始行号
        if checkpoint and checkpoint.is_done(ck_step, key):
            fixed = checkpoint.get_result(ck_step, key)
            if fixed and fixed != paras[j][1]:
                paras[j] = (paras[j][0], fixed)
                repaired += 1
            continue
        context_parts = []
        if j > 0:
            context_parts.append(paras[j - 1][1])
        context_parts.append(f">>> {paras[j][1]} <<<")
        if j + 1 < len(paras):
            context_parts.append(paras[j + 1][1])
        context = "\n\n".join(context_parts)

        prompt = (
            "下面是一段工程规范经 OCR 得到的文本，其中被 `>>> ... <<<` 包裹的段落可能"
            "存在字词错误。请只返回该段落的修正版本（保留原有格式、编号、公式，只改正"
            "明显 OCR 错误），不要添加任何解释。\n\n" + context
        )
        rsp = create_chat_completion(
            client,
            model=model_name,
            max_tokens=2048,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        fixed = rsp.choices[0].message.content.strip() if rsp.choices else ""
        if fixed and fixed != paras[j][1]:
            paras[j] = (paras[j][0], fixed)
            repaired += 1
        if checkpoint:
            checkpoint.mark_done(ck_step, key, result=fixed or paras[j][1])

    # 重建全文：直接把修复后的段落用双换行拼接回去
    # （原始空白行结构在段落列表中被隐式保留 —— 相邻段落之间就是原来的空行位置）
    return "\n\n".join(p for _, p in paras), repaired


def write_dry_run_report(text: str, out_path: Path) -> int:
    """把可疑段落摘出来写成报告。"""
    paras = split_paragraphs(text)
    suspicious = [(j, paras[j][1]) for j, (_, p) in enumerate(paras) if paragraph_suspicious(p)]
    lines = [
        "# 段落级 OCR 修复清单（dry-run）",
        "",
        f"共 {len(suspicious)} 个疑似受损段落。",
        "",
    ]
    for j, para in suspicious:
        lines.append(f"## 段落 #{j + 1}（原文行 {paras[j][0] + 1} 起）")
        lines.append("")
        lines.append("```text")
        lines.append(para)
        lines.append("```")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return len(suspicious)
