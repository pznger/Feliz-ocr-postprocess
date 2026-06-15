"""跨页断句拼接。

OCR 出来的 md 中，有时一个完整句子被页码标记拦腰截断：
    ...对中硬地基取

    <!-- 13 -->

    坚硬地基取 0.15...

此模块负责：
1. 扫描所有 `<!-- N -->` 页码标记
2. 对标记前后的文本片段做规则预判——两端都是中文正文、没有句号等终止符
3. 对候选断句发 LLM 做最终确认并返回拼接结果
4. 把拼接后的文本回填，删除断开的碎片行，保留页码标记

规则层已经能过滤 90% 以上的假阳性（章节头、条款号、表格、公式），
LLM 只对真正可疑的候选做最终裁决，token 消耗可控。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PAGE_RE = re.compile(r"^<!--\s*(\d+)\s*-->$")

# 句子结束符（完整终止）
_SENTENCE_END_RE = re.compile(r"[。；！？」』）\)\"」　]$")

# 前段末尾判定：以中文字 / 英文字母 / 右括号结尾，不以句号等结尾
def _prev_ends_mid(prev: str) -> bool:
    if not prev:
        return False
    last = prev[-1]
    if _SENTENCE_END_RE.search(last):
        return False
    return bool(re.match(r"[一-鿿\w）\)\]}]", last))


# 后段开头判定：以中文字开头，不是标题 / 条款 / 表格 / 公式
def _next_starts_continuation(nxt: str) -> bool:
    if not nxt:
        return False
    if nxt.startswith(("#", "**", "<!--", "$$", "<table", "<tr", "<td", "<th", "```")):
        return False
    if re.match(r"^\d+\.\d+", nxt):  # 条款号
        return False
    return bool(re.match(r"[一-鿿]", nxt[0]))


@dataclass
class SplitCandidate:
    page: int          # 页码
    marker_line: int    # 页码标记所在行号
    prev_text: str      # 前半段
    next_text: str      # 后半段
    prev_line_num: int  # 前半段在原文本中的行号
    next_line_num: int  # 后半段在原文本中的行号


def find_candidates(text: str) -> list[SplitCandidate]:
    """扫描全文，找出所有疑似跨页断句候选。"""
    lines = text.splitlines()
    candidates: list[SplitCandidate] = []

    for i, line in enumerate(lines):
        m = PAGE_RE.match(line.strip())
        if not m:
            continue
        page = int(m.group(1))

        # 向前找最近的非空非标记行
        prev_text = ""
        prev_line = -1
        for j in range(i - 1, max(0, i - 8), -1):
            s = lines[j].strip()
            if s and not PAGE_RE.match(s) and not s.startswith("#"):
                prev_text = s
                prev_line = j
                break

        # 向后找最近的非空非标记行
        next_text = ""
        next_line = -1
        for j in range(i + 1, min(len(lines), i + 8)):
            s = lines[j].strip()
            if s and not PAGE_RE.match(s) and not s.startswith("#"):
                next_text = s
                next_line = j
                break

        if prev_text and next_text and len(prev_text) > 8 and len(next_text) > 8:
            if _prev_ends_mid(prev_text) and _next_starts_continuation(next_text):
                candidates.append(SplitCandidate(
                    page=page, marker_line=i,
                    prev_text=prev_text, next_text=next_text,
                    prev_line_num=prev_line, next_line_num=next_line,
                ))

    return candidates


def apply_joins(text: str, joins: list[tuple[int, str]]) -> str:
    """把 LLM 确认的拼接结果回填到正文。

    joins: [(marker_line, joined_text), ...]
    需要从后往前改，避免行号偏移。
    """
    lines = text.splitlines()
    # 按 marker_line 倒序
    for marker_line, joined_text in sorted(joins, key=lambda x: -x[0]):
        # 找到 prev_line（marker 前第一个非空行）
        prev_line = -1
        for j in range(marker_line - 1, max(0, marker_line - 8), -1):
            if lines[j].strip() and not PAGE_RE.match(lines[j].strip()):
                prev_line = j
                break
        # 找到 next_line（marker 后第一个非空行）
        next_line = -1
        for j in range(marker_line + 1, min(len(lines), marker_line + 8)):
            if lines[j].strip() and not PAGE_RE.match(lines[j].strip()):
                next_line = j
                break

        if prev_line >= 0:
            lines[prev_line] = joined_text
        if next_line >= 0:
            lines[next_line] = ""  # 清空碎片行

    return "\n".join(lines)


def join_with_llm(
    text: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
    on_progress=None,
    checkpoint=None,
) -> tuple[str, int]:
    """对候选断句调 LLM 做二次确认，返回 (拼接后文本, 拼接数)。

    on_progress(current, total, joined_so_far) 每处理一个候选调用一次。
    checkpoint: Checkpoint 实例，支持断点续跑。
    """
    candidates = find_candidates(text)
    total = len(candidates)
    if not candidates or dry_run:
        return text, total

    from .config import get_openai_client, create_chat_completion, FELIZ_MODEL

    client = get_openai_client(base_url=base_url, api_key=api_key)
    model_name = model or FELIZ_MODEL
    ck_step = "page_break_join"
    lines = text.splitlines()
    joins: list[tuple[int, str]] = []

    # 从 checkpoint 恢复已拼接数
    joined_count = checkpoint.step_done_count(ck_step) if checkpoint else 0
    # 先应用已缓存的拼接结果
    if checkpoint:
        for key in list(checkpoint.data.get(ck_step, {}).get("done", {}).keys()):
            result = checkpoint.get_result(ck_step, key)
            if result and result != "NO":
                marker_line = int(key.split(":", 1)[1])
                joins.append((marker_line, result))
    if joins:
        text = apply_joins(text, joins)
        lines = text.splitlines()
        joined_count = len(joins)

    for idx, c in enumerate(candidates, 1):
        # 已缓存的跳过（join_with_llm 在开头已批量应用缓存结果）
        key = f"{c.page}:{c.marker_line}"
        if checkpoint and checkpoint.is_done(ck_step, key):
            continue
        # 取标记前后各 3 行作为上下文
        ctx_start = max(0, c.prev_line_num - 2)
        ctx_end = min(len(lines), c.next_line_num + 3)
        context = "\n".join(lines[ctx_start:ctx_end])

        prompt = (
            "下面是一段工程规范文本，其中包含一个页码标记 `<!-- N -->`。"
            "请判断：标记前后的文字是否属于同一个被分页截断的句子？\n\n"
            "如果是同一句，请只返回拼接后的完整文本（一行，去掉页码标记前后的换行，将前后两段直接衔接）。\n"
            "如果不是同一句，请只返回 `NO`。\n"
            "不要添加任何解释。\n\n"
            f"--- 页码标记位置 ---\n"
            f"前半段末尾: ...{c.prev_text[-40:]}\n"
            f"后半段开头: {c.next_text[:40]}...\n\n"
            f"--- 完整上下文 ---\n{context}"
        )
        rsp = create_chat_completion(
            client,
            model=model_name,
            max_tokens=512,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        result = rsp.choices[0].message.content.strip() if rsp.choices else ""
        if result and result != "NO" and len(result) > len(c.prev_text) * 0.5:
            joins.append((c.marker_line, result))
            joined_count += 1
        if checkpoint:
            checkpoint.mark_done(ck_step, key, result=result if (result and result != "NO") else "NO")
        if on_progress:
            on_progress(idx, total, joined_count)

    if joins:
        text = apply_joins(text, joins)
    return text, joined_count


def write_dry_run_report(text: str, out_path: Path) -> int:
    """把候选断句清单写成报告。"""
    candidates = find_candidates(text)
    lines = [
        "# 跨页断句检测报告",
        "",
        f"共 {len(candidates)} 处疑似跨页断句。",
        "",
    ]
    for i, c in enumerate(candidates, 1):
        lines.append(f"## #{i} (页码 {c.page}，标记行 {c.marker_line + 1})")
        lines.append("")
        lines.append(f"- 前半段末尾: `...{c.prev_text[-60:]}`")
        lines.append(f"- 后半段开头: `{c.next_text[:60]}...`")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return len(candidates)
