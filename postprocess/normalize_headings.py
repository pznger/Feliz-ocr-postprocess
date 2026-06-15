"""标题归一化。

需求复述：
- 只保留两种"加粗标题"：一级（# 章）、二级（## 节）。
- 其它所有 #+ 形式的"标题"（包括 ###/####/##### …）一律转成正文段落。
- 标题层级要按"实际编号"判断，不能机械依赖原始 md 中的井号数量：
    "## 1总 则" -> "# 1 总则"
    "#### 7室内环境" -> "# 7 室内环境"
    "## 11.1-般规定" -> "## 11.1 一般规定"
    "## 6.1.4 ..." -> 普通段落 (三级条款编号)
    "### 1 在人行道上空：" -> 普通段落 (条款项)
    "## 附录 $F$ 加劲钢板剪力墙..." -> "# 附录 F 加劲钢板剪力墙..."
- 标题里的全角空格、$F$ 这种公式包裹、`1总 则` 之间的空格全部归一化掉。

策略：
1. 对每一行，先 strip 掉前导 `#`，得到 candidate。
2. 用一组判定函数确定 candidate 实际是 章节 / 节 / 条款 / 附录 / 附录节 / 其他。
3. 章节 -> `# {n} {name}`；节 -> `## {n.m} {name}`；附录 -> `# 附录 X ...`；附录节 -> `## X.Y ...`；
   条款 (n.m.k) 或 条款项 (### 1 ...) -> 普通段落（直接去掉井号即可，加粗与否由 text_normalize 维护）。
4. 对于看起来不是上述任何模式但又是井号开头的行（章节标题被 OCR 拆成两行、版权页等），保持原内容但降级为正文。
"""

from __future__ import annotations

import re

HEAD_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")

# 全角点号/全角空格替换
def _basic_normalize(text: str) -> str:
    text = text.replace("　", " ").replace("．", ".")
    # 去掉 $...$ 中只有字母或数字的包裹
    text = re.sub(r"\$([A-Za-z0-9.]+)\$", r"\1", text)
    # OCR 把 一 识别成 -
    text = re.sub(r"(\d+\.\d+)\s*-般规定", r"\1 一般规定", text)
    # 修复全角点号被转成 . 后形成的 "10.预应力..." -> "10 预应力..."
    # （原始 OCR "10．预应力…"，．→. 后变成了 "10.预"，需转回空格分隔）
    text = re.sub(r"^(\d{1,2})\.([一-鿿])", r"\1 \2", text)
    return text


def _try_chapter(candidate: str) -> str | None:
    """命中返回 `# n 章节名`，否则 None。"""
    stripped = candidate.strip()

    # 优先匹配带空格的规范形式：`1 总则` / `11 基本规定`
    m2 = re.match(r"^(\d{1,2})\s+([一-鿿].{1,30})$", stripped)
    if m2:
        ch_no, name = m2.group(1), m2.group(2).strip()
        return f"# {ch_no} {name}"

    # 再尝试紧凑形式：`1总则` / `10粘贴纤维复合材加固法`
    compact = re.sub(r"\s+", "", stripped)
    m = re.match(r"^(\d{1,2})([一-鿿].{1,30})$", compact)
    if not m:
        return None
    ch_no, name = m.group(1), m.group(2).strip()

    # 护栏：紧凑形式可能把两项内容合并了
    # 如 "1 9度设防烈度..." 去空格后变成 "19度设防烈度..."，被误判为第19章
    # 检查：原始 candidate 是否以匹配到的章节号开头
    # "1 9度设防…".startswith("19") → False，拒绝
    if not stripped.startswith(ch_no):
        return None

    return f"# {ch_no} {name}"


def _try_section(candidate: str) -> str | None:
    """命中返回 `## n.m 节名`，否则 None。注意必须正好两段编号。"""
    # 正常形式 `1.0` `2.1` `11.1`
    m = re.match(r"^(\d+)\.(\d+)(?=[^.\d])\s*(.*)$", candidate.strip())
    if not m:
        return None
    a, b, rest = m.group(1), m.group(2), m.group(3).strip()
    # 排除条款项 1.0.1 (本应进入 _try_clause，但若 candidate 是 "1.0.1 xxx" 这里也防御一次)
    return f"## {a}.{b} {rest}".rstrip()


def _try_appendix(candidate: str) -> str | None:
    m = re.match(r"^附录\s*([A-Z])\s*(.*)$", candidate.strip())
    if not m:
        return None
    letter, rest = m.group(1), m.group(2).strip()
    return f"# 附录 {letter} {rest}".rstrip()


def _try_appendix_section(candidate: str) -> str | None:
    m = re.match(r"^([A-Z])\.(\d+)(?=[^.\d])\s*(.*)$", candidate.strip())
    if not m:
        return None
    letter, b, rest = m.group(1), m.group(2), m.group(3).strip()
    return f"## {letter}.{b} {rest}".rstrip()


def _is_clause_or_list_item(candidate: str) -> bool:
    """命中说明这是条款 n.m.k 或条款列表项 `1 在人行道上空：` 之类。"""
    if re.match(r"^\d+\.\d+\.\d+", candidate.strip()):
        return True
    # 列表项：单数字 + 空格 + 以数字开头的内容，如 "1 9度设防烈度…"
    # 这类容易被 _try_chapter 的紧凑匹配误判为章节
    if re.match(r"^\d+\s+\d", candidate.strip()):
        return True
    if re.match(r"^\d+\s+[一-鿿].*[：:]$", candidate.strip()):
        return True
    return False


# ── 标题跨行合并 ──────────────────────────────────────────

# 附录标题被 OCR 拆成两行的模式：
#   ## 附录A 钢筋的公称直径、
#   ### 公称截面面积及理论重量
_APPENDIX_SPLIT_RE = re.compile(
    r"^(#{1,6})\s*附录\s*([A-Z])\s+(.+[、与和,])$"
)

# 章标题被 OCR 拆成两行：如 "4材" + "料" -> "4材料"
#   匹配 "N材" 这种截断形式（N=1-2位数字，紧跟一个中文字后被截断）
_CHAPTER_SPLIT_RE = re.compile(
    r"^(#{1,6})\s*(\d{1,2})([一-鿿]{1,3})$"
)


def _join_split_headings(text: str) -> str:
    """合并被 OCR 拆成两行的附录/章节标题。"""
    lines = text.splitlines()
    merged: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ── 附录标题截断 ──
        m = _APPENDIX_SPLIT_RE.match(stripped)
        if m:
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and lines[j].strip().startswith("#"):
                next_text = re.sub(r"^#{1,6}\s*", "", lines[j].strip())
                hashes = m.group(1)
                letter = m.group(2)
                prefix_part = m.group(3)
                merged.append(f"{hashes} 附录{letter} {prefix_part}{next_text}")
                i = j + 1
                continue
            merged.append(line)
            i += 1
            continue

        # ── 章标题截断（如 "4材" + "料" -> "4材料"） ──
        m2 = _CHAPTER_SPLIT_RE.match(stripped)
        if m2:
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines):
                next_stripped = lines[j].strip()
                if next_stripped.startswith("#"):
                    next_text = re.sub(r"^#{1,6}\s*", "", next_stripped)
                    # 只合并纯中文片段（不含数字、不含标点），避免错误合并
                    if re.match(r"^[一-鿿]{1,4}$", next_text):
                        hashes = m2.group(1)
                        ch_no = m2.group(2)
                        partial = m2.group(3)
                        merged.append(f"{hashes} {ch_no}{partial}{next_text}")
                        i = j + 1
                        continue
            merged.append(line)
            i += 1
            continue

        merged.append(line)
        i += 1
    return "\n".join(merged)


def normalize_headings(text: str) -> str:
    text = _join_split_headings(text)
    out: list[str] = []
    seen_chapters: set[int] = set()  # 已出现的章节号，防止条款编号被误升为章节
    for raw in text.splitlines():
        m = HEAD_RE.match(raw)
        if not m:
            out.append(raw)
            continue
        hashes = m.group(1)
        hash_count = len(hashes)
        candidate = _basic_normalize(m.group(2))
        # 先尝试附录系列（关键词更明显）
        promoted = (
            _try_appendix(candidate)
            or _try_appendix_section(candidate)
        )
        if promoted is None and not _is_clause_or_list_item(candidate):
            # 章节候选：对较深层级（###以上）仅当该编号尚未出现时才提升
            ch = _try_chapter(candidate)
            if ch:
                ch_num = int(re.match(r"^# (\d+)", ch).group(1))
                if ch_num not in seen_chapters:
                    seen_chapters.add(ch_num)
                    promoted = ch
                # else: 编号重复，多半是章内条款编号，丢弃
            if promoted is None:
                promoted = _try_section(candidate)
        if promoted is None:
            # 是井号开头但既不是章节也不是节 -> 降级为正文（保留候选文字）
            stripped = candidate.strip()
            # 三级条款编号 `x.y.z`：保留为 ### 三级标题，供 splitter 识别目录
            cls_m = re.match(r"^(\d+\.\d+\.\d+)(\s*.*)?$", stripped)
            if cls_m:
                num = cls_m.group(1)
                tail = (cls_m.group(2) or "").strip()
                promoted = f"### {num} {tail}".rstrip()
            else:
                out.append(stripped)
        if promoted is not None:
            out.append(promoted)
    return "\n".join(out)
