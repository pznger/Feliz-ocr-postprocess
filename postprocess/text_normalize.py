"""文本归一化。

覆盖 wiki-拆分指导.md 第 2.3 节列出的所有 OCR 噪声形式，并复用
`scripts/clean_raw_md.py` 中已验证的合并/页码/全角符号规则。

实现要点：
1. 行级 substitution：
   - `1． $0\\mathrm{m}^{3}$` / `1. $0\\mathrm{m}^{3}$` -> `1.0...`（条款编号被打散）
   - `3． $5\\%$` -> `3.5%`
   - `7.1.1 0` / `7.1.1 7` -> `7.1.10` / `7.1.17`
   - `$2.0m^{2}$ 、的` -> `$2.0m^{2}$ 的`
   - `$25\\mathrm {\\;Pa}\\;30\\mathrm {\\;Pa}$` -> `$25\\mathrm{\\;Pa}~30\\mathrm{\\;Pa}$`
   - `1总 则` -> `1 总则`
   - 中文标题字符之间的内嵌空格 / 全角空格统一
2. 页码标记 `<!-- ·7· -->` 也归一化成 `<!-- 7 -->`，便于后续脚本计数。
3. 把连续多个空白行压缩成一个。
4. bold_clause_numbers：对正文中的条款编号统一加粗（此函数应在标题归一化之后调用）。
"""

from __future__ import annotations

import re

# 页码 `<!-- ·7· -->` / `<!-- 7. -->` / `<!-- 7 -->`
PAGE_VARIANT_RE = re.compile(r"<!--\s*[·•.]*\s*(\d+)\s*[·•.]*\s*-->")

# 条款编号被 OCR 打散
CLAUSE_BROKEN_RE = re.compile(r"(\d+\.\d+\.\d+)\s+(\d)(?=\b|[^\d])")
# `1． $0\mathrm{...}$` 或 `1. $0\mathrm{...}$`
NUM_FORMULA_RE = re.compile(r"(\d)[.．]\s*\$\s*(\d)")
# `3． $5\%$` -> `3.5%`
NUM_PERCENT_RE = re.compile(r"(\d)[.．]\s*\$\s*(\d+)\s*\\\s*%\s*\$")
# 区间连接符丢失：两个相邻的同类型单位之间应该是 `~`
INTERVAL_LOST_RE = re.compile(
    r"(\$[^$]*?\\mathrm\s*\{\\;[A-Za-z]+\})\s*\\;\s*(\d+\s*\\mathrm\s*\{\\;[A-Za-z]+\}\$)"
)
# 公式后接顿号再接的（最小修补）
PUNCT_AFTER_FORMULA_RE = re.compile(r"(\$[^$\n]+\$)\s*、\s*(的|和|或|时|后|前)")

# 标题里 `1总 则` 风格
TITLE_INNER_SPACE_RE = re.compile(r"^(\d+(?:\.\d+){0,2})\s*([一-鿿])\s+([一-鿿]+)")

# ── 条款编号加粗（在标题归一化之后使用） ──────────────────

# OCR 原材料中自带的裸数字加粗：**1** 或 **12**（不是 x.y.z）
_BARE_BOLD_RE = re.compile(r"^\*\*(\d{1,2})\*\*\s+")

# 已加粗的条款号：**1.0.1** —— 这种是合法的，不要动
_CLAUSE_BOLD_RE = re.compile(r"^\*\*(\d+\.\d+\.\d+)\*\*")

# 未加粗的条款编号：x.y.z 后跟空格和正文
_CLAUSE_NUM_RE = re.compile(r"^(\d+\.\d+\.\d+)(?=\s+[^\d\s])")


def _should_skip(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if s.startswith(("#", "<!--", "<table", "<tr", "<td", "<th", "</table", "$$")):
        return True
    return False


def strip_bare_number_bold(text: str) -> str:
    """去掉 OCR 原材料中自带的裸数字加粗（**1** → 1）。

    只处理行首 `**N** ` 这种裸数字，不碰 `**x.y.z**` 条款编号。
    必须在 normalize_headings 之后调用。
    """
    out: list[str] = []
    for line in text.splitlines():
        if _should_skip(line):
            out.append(line)
            continue
        s = line.strip()

        # **x.y.z** 条款编号 —— 保留
        if _CLAUSE_BOLD_RE.match(s):
            out.append(line)
            continue

        # **N** 裸数字 —— 去加粗
        m = _BARE_BOLD_RE.match(s)
        if m:
            leading = line[: len(line) - len(line.lstrip())]
            rest = s[m.end():]
            out.append(f"{leading}{m.group(1)} {rest}")
            continue

        out.append(line)
    return "\n".join(out)


def bold_clause_numbers(text: str) -> str:
    """给未加粗的 x.y.z 条款编号加上 **粗体**。

    必须在 normalize_headings 之后调用。
    """
    out: list[str] = []
    for line in text.splitlines():
        if _should_skip(line):
            out.append(line)
            continue

        s = line.strip()

        # 已经加粗或已经是裸数字加粗的跳过（裸数字加粗已由 strip_bare_number_bold 清理）
        if _CLAUSE_BOLD_RE.match(s) or _BARE_BOLD_RE.match(s):
            out.append(line)
            continue

        # 未加粗的 x.y.z → **x.y.z**
        m = _CLAUSE_NUM_RE.match(s)
        if m:
            num = m.group(1)
            leading = line[: len(line) - len(line.lstrip())]
            rest = s[m.end():]
            out.append(f"{leading}**{num}**{rest}")
            continue

        out.append(line)
    return "\n".join(out)

# 在 strip 后判断
def _normalize_page_markers(line: str) -> str:
    m = PAGE_VARIANT_RE.search(line)
    if not m:
        return line
    return PAGE_VARIANT_RE.sub(lambda mo: f"<!-- {mo.group(1)} -->", line)


def _normalize_clause_break(text: str) -> str:
    return CLAUSE_BROKEN_RE.sub(r"\1\2", text)


def _normalize_num_formula(text: str) -> str:
    # `1. $0\mathrm{m}^{3}$` -> `1.0$\mathrm{m}^{3}$`
    text = NUM_PERCENT_RE.sub(r"\1.\2%", text)
    text = NUM_FORMULA_RE.sub(r"\1.\2$", text)
    return text


def _normalize_interval(text: str) -> str:
    return INTERVAL_LOST_RE.sub(r"\1~\2", text)


def _normalize_punct_after_formula(text: str) -> str:
    return PUNCT_AFTER_FORMULA_RE.sub(r"\1 \2", text)


def _normalize_title_inner_space(line: str) -> str:
    if not line.startswith("#"):
        return line
    head_m = re.match(r"^(#{1,6})\s*(.+)$", line)
    if not head_m:
        return line
    hashes, body = head_m.group(1), head_m.group(2)
    body = body.replace("　", " ")
    # `1总 则` -> `1 总则`
    m = re.match(r"^(\d+(?:\.\d+){0,2})\s*([一-鿿])\s+([一-鿿]+)$", body.strip())
    if m:
        return f"{hashes} {m.group(1)} {m.group(2)}{m.group(3)}"
    return f"{hashes} {body.strip()}"


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


def _inline_formula_images(text: str) -> str:
    """将 OCR 已识别的公式图片还原为行内公式文本。

    OCR 引擎将部分公式渲染为图片，同时在 HTML 注释中保留了
    提取到的 LaTeX 文本。直接提取注释中的公式，丢弃图片链接。
    注释中的前导页码数字（如 "2"）会被去除。
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("<!--") and "$" in stripped:
            # 提取注释中的 LaTeX，去掉前导页码数字
            comment_text = stripped[4:-3].strip() if stripped.endswith("-->") else stripped[4:].strip()
            # 去掉前导的纯数字页码标记，如 "2 $F_{1}$" -> "$F_{1}$"
            formula = re.sub(r"^\d{1,4}\s+", "", comment_text)
            # 找下一非空行，确认是图片链接则跳过
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and lines[j].strip().startswith("![]("):
                out.append(formula)
                i = j + 1
                continue
        out.append(line)
        i += 1
    return "\n".join(out)


def normalize_text(text: str) -> str:
    """正文级别的归一化。"""
    text = _normalize_clause_break(text)
    text = _normalize_num_formula(text)
    text = _normalize_interval(text)
    text = _normalize_punct_after_formula(text)
    text = "\n".join(_normalize_page_markers(ln) for ln in text.splitlines())
    text = "\n".join(_normalize_title_inner_space(ln) for ln in text.splitlines())
    text = _inline_formula_images(text)
    text = _collapse_blank_lines(text)
    return text
