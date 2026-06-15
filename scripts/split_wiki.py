#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path


PAGE_RE = re.compile(r"^<!--\s*[·•.]?\s*(\d+)\s*[·•.]?\s*-->$")
H1_RE = re.compile(r"^#(?!#)\s*(.+?)\s*$")
H2_RE = re.compile(r"^##(?!#)\s*(.+?)\s*$")
H3_RE = re.compile(r"^###(?!#)\s*(.+?)\s*$")
CHAPTER_NO_RE = re.compile(r"^(\d+)(?=\s|[^\d.])\s*(.+)$")
SECTION_NO_RE = re.compile(r"^(\d+\.\d+)(?=\s|[^\d.])\s*(.*)$")
SUBSECTION_NO_RE = re.compile(r"^(\d+\.\d+\.\d+)(?=\s|[^\d.])\s*(.*)$")
APPENDIX_RE = re.compile(r"^附录\s*([A-Z])\s*(.*)$")
APPENDIX_SECTION_RE = re.compile(r"^([A-Z]\.\d+)(?=\s|[^\d.])\s*(.*)$")
APPENDIX_SUBSECTION_RE = re.compile(r"^([A-Z]\.\d+\.\d+)(?=\s|[^\d.])\s*(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
TAIL_HEADING_RE = re.compile(
    r"^(本规范用词说明|本标准用词说明|引用标准名录|条文说明|修订说明|编制说明|目次)$"
)
SPLIT_CHAPTER_RE = re.compile(r"^(\d+)材$")
CLAUSE_LIST_ITEM_RE = re.compile(r"^\d+\s+[\u4e00-\u9fff].*[：:]$")
ROMAN_LIST_ITEM_RE = re.compile(r"^[IVXLC]+\s+[\u4e00-\u9fff]", re.I)


@dataclass
class SubSection:
    title: str
    start_idx: int
    start_page: int
    end_idx: int = 0


@dataclass
class Section:
    title: str
    start_idx: int
    start_page: int
    subsections: list[SubSection] = field(default_factory=list)
    end_idx: int = 0


@dataclass
class Chapter:
    title: str
    start_idx: int
    start_page: int
    sections: list[Section] = field(default_factory=list)
    end_idx: int = 0


def normalize_title(text: str) -> str:
    text = normalize_inline_ocr(text.strip().replace("\u3000", " "))
    text = text.replace("．", ".")
    text = re.sub(r"\$([0-9.]+)\$", r"\1", text)
    text = re.sub(r"\$([A-Za-z])\$", r"\1", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(\d+)\.\s*(?=[\u4e00-\u9fff])", r"\1 ", text)
    numbered_match = re.match(r"^([0-9.\s]+)(.*)$", text)
    if numbered_match and "." in numbered_match.group(1):
        number = re.sub(r"\s+", "", numbered_match.group(1))
        rest = numbered_match.group(2).strip()
        text = f"{number} {rest}".strip()
    text = re.sub(r"^(\d+)(?=[\u4e00-\u9fff])", r"\1 ", text)
    text = re.sub(r"^(\d+\.\d+\.\d+)(?=[^\d.\s])", r"\1 ", text)
    text = re.sub(r"^(\d+\.\d+)(?=[^\d.\s])", r"\1 ", text)
    text = re.sub(r"^(\d+\.\d+)\s*-般规定$", r"\1 一般规定", text)
    return text


def slug_title(text: str) -> str:
    text = normalize_title(text)
    text = re.sub(r"\$[^$]*\$", "", text)
    text = re.sub(r"\\[a-zA-Z]+(\{[^}]*\})?", "", text)
    text = re.sub(r"\s+", "", text)
    text = text.replace("/", "／").replace(":", "：")
    if len(text) > 72:
        text = text[:72]
    return text


def is_chapter_title(title: str) -> bool:
    return bool(CHAPTER_NO_RE.match(title)) and not title.endswith(("：", ":"))


def chapter_number(title: str) -> int | None:
    match = CHAPTER_NO_RE.match(title)
    if not match or title.endswith(("：", ":")):
        return None
    return int(match.group(1))


def is_appendix_title(title: str) -> bool:
    return bool(APPENDIX_RE.match(title))


def is_tail_heading(title: str) -> bool:
    return bool(TAIL_HEADING_RE.match(title.replace(" ", "")))


def is_clause_list_item(title: str) -> bool:
    return bool(CLAUSE_LIST_ITEM_RE.match(title) or ROMAN_LIST_ITEM_RE.match(title))


def merge_split_chapter_title(content: list[str], idx: int, title: str) -> tuple[str, int]:
    compact = title.replace(" ", "")
    match = SPLIT_CHAPTER_RE.match(compact)
    if not match:
        return title, idx
    for next_idx in range(idx + 1, min(idx + 5, len(content))):
        raw = content[next_idx]
        if not raw.strip():
            continue
        heading_match = HEADING_RE.match(raw)
        if not heading_match:
            break
        continuation = normalize_title(heading_match.group(2))
        if continuation.replace(" ", "") == "料":
            return f"{match.group(1)} 材料", next_idx
        break
    return title, idx


def should_append_section(title: str, current_chapter: Chapter | None) -> bool:
    return bool(current_chapter and is_section_title(title, current_chapter))


def is_misplaced_section_title(title: str, current_chapter: Chapter | None) -> bool:
    if should_append_section(title, current_chapter):
        return True
    if SUBSECTION_NO_RE.match(title) or APPENDIX_SUBSECTION_RE.match(title):
        return True
    if SECTION_NO_RE.match(title) and current_chapter and chapter_number(current_chapter.title) is not None:
        section_match = SECTION_NO_RE.match(title)
        chapter_no = chapter_number(current_chapter.title)
        return bool(section_match and section_match.group(1).startswith(f"{chapter_no}."))
    return False


def should_end_content(title: str, level: int, current_chapter: Chapter | None) -> bool:
    if is_tail_heading(title):
        return True
    if level != 1:
        return False
    if is_chapter_title(title) or is_appendix_title(title):
        return False
    if is_misplaced_section_title(title, current_chapter):
        return False
    if is_clause_list_item(title):
        return False
    return False


def is_section_title(title: str, current_chapter: Chapter | None = None) -> bool:
    if current_chapter and is_appendix_title(current_chapter.title):
        chapter_match = APPENDIX_RE.match(current_chapter.title)
        section_match = APPENDIX_SECTION_RE.match(title)
        return bool(
            chapter_match
            and section_match
            and section_match.group(1).startswith(f"{chapter_match.group(1)}.")
        )
    if current_chapter:
        chapter_no = chapter_number(current_chapter.title)
        section_match = SECTION_NO_RE.match(title)
        return bool(
            chapter_no is not None
            and section_match
            and section_match.group(1).startswith(f"{chapter_no}.")
        )
    if SECTION_NO_RE.match(title):
        return True
    return False


def is_subsection_title(title: str, section_number: str | None = None) -> bool:
    """判断标题是否为三级条款（x.y.z），且前缀需匹配当前节号。"""
    sub_match = SUBSECTION_NO_RE.match(title)
    if not sub_match:
        if APPENDIX_SUBSECTION_RE.match(title):
            return True
        if section_number:
            return bool(re.match(rf"^{re.escape(section_number)}\.\d+", title))
        return False
    # 有 section_number 时校验前缀：x.y.z 应属于 x.y 节
    if section_number:
        return title.startswith(section_number + ".")
    return True


def appendix_full_title(content: list[str], idx: int, title: str) -> str:
    for next_idx in range(idx + 1, min(idx + 4, len(content))):
        raw = content[next_idx]
        if not raw.strip():
            continue
        heading_match = HEADING_RE.match(raw)
        if not heading_match:
            break
        continuation = normalize_title(heading_match.group(2))
        if (
            is_tail_heading(continuation)
            or is_appendix_title(continuation)
            or chapter_number(continuation) is not None
            or APPENDIX_SECTION_RE.match(continuation)
            or APPENDIX_SUBSECTION_RE.match(continuation)
        ):
            break
        return normalize_title(f"{title}{continuation}")
    return title


def normalize_inline_ocr(text: str) -> str:
    text = re.sub(r"(\d+)\s*[．.]\s+\$([0-9][^$]*)\$", r"$\1.\2$", text)
    text = re.sub(r"(\$\s*[^$]+?\$)\s*、\s*的", r"\1 的", text)
    text = text.replace(
        "$25\\mathrm {\\;Pa}\\;30\\mathrm {\\;Pa}$",
        "$25\\mathrm {\\;Pa}~30\\mathrm {\\;Pa}$",
    )
    # 渲染兼容性：去掉 LaTeX 排版命令与紧跟其后花括号 / 下上标记号之间的空白
    # `\mathrm {fe}` → `\mathrm{fe}`、`\alpha _{s}` → `\alpha_{s}`
    # 部分 markdown / KaTeX 渲染器在命令和 { / _ / ^ 之间有空格时会把命令名当纯文本
    text = re.sub(
        r"\\(mathrm|mathbf|mathit|mathcal|mathfrak|mathbb|mathsf|mathtt|text|textbf|textit|textrm|operatorname|boldsymbol|pmb)\s+\{",
        lambda m: f"\\{m.group(1)}{{", text
    )
    text = re.sub(r"\\([a-zA-Z]+)\s+(?=[_^]\s*[{\[(])", lambda m: f"\\{m.group(1)}", text)
    return text


def catalog_title(text: str) -> str:
    return normalize_title(text).replace(" ", "")


def first_content_index(lines: list[str]) -> int:
    for idx, line in enumerate(lines):
        heading_match = HEADING_RE.match(line)
        if not heading_match:
            continue
        title = normalize_title(heading_match.group(2))
        chapter_match = CHAPTER_NO_RE.match(title) if is_chapter_title(title) else None
        if chapter_match and chapter_match.group(1) == "1":
            return idx
    raise ValueError("未找到从 # 1 开始的正文部分")


def parse_structure(lines: list[str]) -> list[Chapter]:
    start = first_content_index(lines)
    content = lines[start:]
    chapters: list[Chapter] = []
    current_page = 1
    current_chapter: Chapter | None = None
    content_end_idx = len(content)
    expected_chapter_no = 1

    skip_lines: set[int] = set()

    for idx, raw in enumerate(content):
        if idx in skip_lines:
            continue
        page_match = PAGE_RE.match(raw)
        if page_match:
            page_no = int(page_match.group(1))
            if page_no >= current_page:
                current_page = page_no
            continue

        heading_match = HEADING_RE.match(raw)
        if not heading_match:
            # 检查非标题行：行首 **x.y.z** 格式的条款，且在当前位置匹配当前节
            if current_chapter and current_chapter.sections:
                bm = re.match(r"^\*\*(\d+\.\d+\.\d+)\*\*\s+(.*)", raw.strip())
                if bm:
                    clause_num = bm.group(1)
                    clause_rest = bm.group(2)
                    section_num = current_chapter.sections[-1].title.split()[0] if current_chapter.sections[-1].title.split() else ""
                    # 前缀校验：x.y.z 的 x.y 必须等于当前节号
                    parts = clause_num.rsplit(".", 1)
                    if len(parts) == 2 and parts[0] == section_num:
                        current_chapter.sections[-1].subsections.append(
                            SubSection(title=f"{clause_num} {clause_rest}", start_idx=idx, start_page=current_page)
                        )
            continue

        level = len(heading_match.group(1))
        title = normalize_title(heading_match.group(2))
        title, merged_end = merge_split_chapter_title(content, idx, title)
        if merged_end > idx:
            for skip_idx in range(idx + 1, merged_end + 1):
                skip_lines.add(skip_idx)
        if should_end_content(title, level, current_chapter):
            content_end_idx = idx
            break
        if is_appendix_title(title):
            current_chapter = Chapter(
                title=appendix_full_title(content, idx, title),
                start_idx=idx,
                start_page=current_page,
            )
            chapters.append(current_chapter)
            continue
        current_chapter_no = chapter_number(title)
        if current_chapter_no == expected_chapter_no:
            current_chapter = Chapter(title=title, start_idx=idx, start_page=current_page)
            chapters.append(current_chapter)
            expected_chapter_no += 1
            continue
        if should_append_section(title, current_chapter):
            current_section = Section(title=title, start_idx=idx, start_page=current_page)
            current_chapter.sections.append(current_section)
            continue
        if current_chapter and current_chapter.sections and is_subsection_title(title, current_chapter.sections[-1].title.split()[0] if current_chapter.sections[-1].title.split() else None):
            current_chapter.sections[-1].subsections.append(
                SubSection(title=title, start_idx=idx, start_page=current_page)
            )

    for chapter_idx, chapter in enumerate(chapters):
        chapter.end_idx = (
            chapters[chapter_idx + 1].start_idx
            if chapter_idx + 1 < len(chapters)
            else content_end_idx
        )
        for sec_idx, section in enumerate(chapter.sections):
            section.end_idx = (
                chapter.sections[sec_idx + 1].start_idx
                if sec_idx + 1 < len(chapter.sections)
                else chapter.end_idx
            )
            for sub_idx, subsection in enumerate(section.subsections):
                subsection.end_idx = (
                    section.subsections[sub_idx + 1].start_idx
                    if sub_idx + 1 < len(section.subsections)
                    else section.end_idx
                )

    return chapters


def pages_in_segment(lines: list[str], start_page: int) -> list[int]:
    pages: list[int] = []
    current_page = start_page
    for line in lines:
        match = PAGE_RE.match(line)
        if match:
            page_no = int(match.group(1))
            if page_no >= current_page:
                current_page = page_no
            continue
        if not line.strip():
            continue
        if not pages or pages[-1] != current_page:
            pages.append(current_page)
    return pages


def split_section_chunks(section: Section, content: list[str], max_pages: int) -> list[dict]:
    segment = content[section.start_idx : section.end_idx]
    pages = pages_in_segment(segment, section.start_page)
    if not pages:
        return [
            {
                "suffix": "",
                "start_idx": section.start_idx,
                "end_idx": section.end_idx,
                "start_page": section.start_page,
                "end_page": section.start_page,
                "part_no": 1,
                "parts": 1,
            }
        ]

    page_count = pages[-1] - section.start_page + 1
    if page_count <= max_pages:
        return [
            {
                "suffix": "",
                "start_idx": section.start_idx,
                "end_idx": section.end_idx,
                "start_page": section.start_page,
                "end_page": pages[-1],
                "part_no": 1,
                "parts": 1,
            }
        ]

    ranges = []
    start_page = section.start_page
    while start_page <= pages[-1]:
        end_page = min(start_page + max_pages - 1, pages[-1])
        ranges.append((start_page, end_page))
        start_page = end_page + 1

    chunks = []
    for part_no, (page_start, page_end) in enumerate(ranges, 1):
        actual_start_page = page_start if part_no == 1 else max(page_start - 1, section.start_page)
        first_line = section.start_idx
        last_line = section.end_idx
        seen = False
        current_page = section.start_page
        for idx in range(section.start_idx, section.end_idx):
            match = PAGE_RE.match(content[idx])
            if not match:
                continue
            page_no = int(match.group(1))
            if page_no < current_page:
                continue
            current_page = page_no
            if not seen and current_page >= actual_start_page:
                first_line = idx
                seen = True
            elif seen and current_page > page_end:
                last_line = idx
                break
        if part_no == 1:
            first_line = section.start_idx
        chunks.append(
            {
                "suffix": f"-{part_no}",
                "start_idx": first_line,
                "end_idx": last_line,
                "start_page": actual_start_page,
                "end_page": page_end,
                "part_no": part_no,
                "parts": len(ranges),
            }
        )
    return chunks


def clean_heading(level: int, line: str, section_number: str | None = None) -> str:
    title = normalize_title(re.sub(r"^#+\s*", "", line).strip())
    if section_number is None and is_chapter_title(title):
        return f"# {title}"
    elif section_number is None and is_appendix_title(title):
        return f"# {title}"
    elif re.match(r"^\d+\s", title) and level >= 2:
        return title
    elif is_subsection_title(title, section_number):
        return title  # 三级条款纯文本，不加粗
    elif SECTION_NO_RE.match(title) or APPENDIX_SECTION_RE.match(title):
        return f"## {title}"
    elif level >= 3:
        return title
    return f"{'#' * level} {title}"


def _can_merge_body_lines(prev: str, curr: str) -> bool:
    p, c = prev.strip(), curr.strip()
    if not p or not c:
        return False
    if p.startswith("###") and not c.startswith("#"):
        if not re.search(r"[。；！？]$", p):
            return True
    if p.startswith("#") or c.startswith("#"):
        return False
    if c.startswith("<table") or p.startswith("<table"):
        return False
    if re.match(r"^\*\*\d+\.\d+\.\d+\*\*", c):
        return False
    if re.match(r"^(\d+)\s+[\u4e00-\u9fff]", c):
        return False
    if re.match(r"^<!--", p) or re.match(r"^<!--", c):
        return False
    if re.search(r'[。；：！？）」』"\'％%]$', p):
        return False
    if re.fullmatch(r"\d+\.?", c):
        return False
    if re.fullmatch(r"\d+\.?", p) and not re.match(r"^[\u4e00-\u9fff]", c):
        return False
    if re.fullmatch(r"\d{1,3}", p) and int(p) < 400 and not re.match(r"^[\u4e00-\u9fff]", c):
        return False
    if c in {"式中：", "式中:"} or c.startswith("式中"):
        return False
    if c.startswith("```") or p.startswith("```"):
        return False
    if re.match(r"^(?:\*\*)?(?:续)?表\s*[\d]", c) or re.match(r"^续表", c):
        return False
    return True


def _join_body_lines(a: str, b: str) -> str:
    a, b = a.rstrip(), b.lstrip()
    if re.fullmatch(r"\d+\.?", a) and re.match(r"^[\u4e00-\u9fff]", b):
        return f"{a} {b}"
    if a.endswith(("-", "—")):
        return a[:-1] + b
    if a and b and (a[-1].isascii() or (b[0].isascii() and not b[0].isdigit())):
        return a + " " + b
    return a + b


def _collapse_blank_gaps(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if out and j < len(lines) and _can_merge_body_lines(out[-1], lines[j]):
                out[-1] = _join_body_lines(out[-1], lines[j])
                i = j + 1
                continue
            if out and out[-1].strip() != "":
                out.append(line)
            i += 1
            continue
        out.append(line)
        i += 1
    return out


def render_body(lines: list[str], section_number: str | None = None) -> str:
    rendered: list[str] = []
    for line in lines:
        if PAGE_RE.match(line):
            continue
        if line.strip() in {"浏览专", "住房城"}:
            continue
        if re.fullmatch(r"\d{1,3}", line.strip()) and int(line.strip()) < 400:
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            rendered.append(clean_heading(level, line, section_number))
        else:
            body_line = normalize_inline_ocr(line.rstrip())
            # 去掉条款编号加粗和不必要的加粗
            body_line = re.sub(r"\*\*(\d+\.\d+\.\d+)\*\*", r"\1", body_line)
            body_line = re.sub(r"\*\*(\d{1,2})\*\*", r"\1", body_line)
            rendered.append(body_line)

    rendered = _collapse_blank_gaps(rendered)
    merged: list[str] = []
    for line in rendered:
        if line.strip() == "":
            if merged and merged[-1].strip() != "":
                merged.append(line)
            continue
        if merged and merged[-1].strip() and _can_merge_body_lines(merged[-1], line):
            merged[-1] = _join_body_lines(merged[-1], line)
        else:
            merged.append(line)

    compact: list[str] = []
    prev_blank = False
    for line in merged:
        blank = line.strip() == ""
        if blank and prev_blank:
            continue
        compact.append(line)
        prev_blank = blank
    return "\n".join(compact).strip() + "\n"


def extract_points(lines: list[str], limit: int = 4) -> list[str]:
    points: list[str] = []
    for line in lines:
        text = line.strip().replace("**", "")
        if not text or text.startswith("#") or PAGE_RE.match(text):
            continue
        if text.startswith("表"):
            points.append(text)
        elif re.match(r"^\d+\.\d+\.\d+", text):
            points.append(text)
        elif re.match(r"^\d+\s", text):
            points.append(text)
        elif "<table" in text and "包含表格内容。" not in points:
            points.append("包含表格内容。")
        if len(points) >= limit:
            break
    return points or ["本文件为规范原文拆分内容。"]


def extract_subtopics(lines: list[str], section_number: str | None = None) -> list[str]:
    topics: list[str] = []
    for line in lines:
        match = HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = normalize_title(match.group(2))
        if level < 3:
            continue
        if re.match(r"^\d+\s", title):
            continue
        if is_subsection_title(title, section_number):
            topics.append(title.replace("**", ""))
    return topics


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_todo_index(title: str, catalog_lines: list[str]) -> str:
    """生成文件级 index，章节目录使用 ### 三级标题，每个条目独立摘要。"""
    lines = [f"# {title}索引", "", "## 章节目录", ""]
    lines.extend(catalog_lines)
    lines.extend(["", "## 各章节内容摘要", ""])
    # 提取 ### 条目为每个生成独立摘要占位
    entries = [l for l in catalog_lines if l.startswith("### ")]
    if entries:
        for entry in entries:
            lines.append(entry)
            lines.append("")
            lines.append("TODO: 待LLM总结")
            lines.append("")
    else:
        lines.append("TODO: 待LLM总结")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_root_index(src_name: str, chapters: list[Chapter]) -> str:
    """生成根 index，章节目录：节用 ###，条款用缩进 - 列表。"""
    lines = [f"# {src_name}索引", "", "## 章节目录", ""]
    all_leaves: list[str] = []

    for chapter in chapters:
        chapter_title = catalog_title(chapter.title)
        if not chapter.sections:
            all_leaves.append(chapter_title)
            lines.append(f"### {chapter_title}")
        else:
            for section in chapter.sections:
                section_title = catalog_title(section.title)
                lines.append(f"### {section_title}")
                if section.subsections:
                    all_leaves.extend(catalog_title(sub.title) for sub in section.subsections)
                    for sub in section.subsections:
                        lines.append(f"- {catalog_title(sub.title)}")
                else:
                    all_leaves.append(section_title)

    lines.extend(["", "## 各章节内容摘要", ""])
    for leaf_title in all_leaves:
        lines.append(f"### {leaf_title}")
        lines.append("")
        lines.append("TODO: 待LLM总结")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_file_catalog_lines(
    chapter_title: str,
    file_name: str,
    section_title: str | None = None,
    subtopics: list[str] | None = None,
    extra_lines: list[str] | None = None,
    subsections: list[SubSection] | None = None,
) -> list[str]:
    """生成文件级章节目录行：节用 ###，子条款用缩进 - 列表。"""
    _ = file_name
    current_title = catalog_title(section_title or chapter_title)
    lines = [f"### {current_title}"]
    if subsections:
        for sub in subsections:
            lines.append(f"- {catalog_title(sub.title)}")
    elif subtopics:
        for subtopic in subtopics:
            lines.append(f"- {catalog_title(subtopic)}")
    if extra_lines:
        lines.extend(extra_lines)
    return lines


def build_indexes(src_name: str, out_root: Path, content: list[str], chapters: list[Chapter], max_pages: int) -> None:
    for chapter in chapters:
        chapter_title = normalize_title(chapter.title)
        chapter_dir = out_root / slug_title(chapter_title)
        chapter_dir.mkdir(parents=True, exist_ok=True)
        chapter_segment = content[chapter.start_idx : chapter.end_idx]

        if not chapter.sections:
            chapter_match = CHAPTER_NO_RE.match(chapter_title)
            file_stem = chapter_match.group(2) if chapter_match else chapter_title
            body_name = slug_title(file_stem)
            write_text(chapter_dir / f"{body_name}.md", render_body(chapter_segment))

            file_index = build_todo_index(
                body_name,
                build_file_catalog_lines(chapter_title, f"{body_name}.md"),
            )
            write_text(chapter_dir / f"{body_name}-index.md", file_index)
            continue

        for section in chapter.sections:
            section_title = normalize_title(section.title)
            section_number = section_title.split()[0]
            for chunk in split_section_chunks(section, content, max_pages):
                chunk_lines = content[chunk["start_idx"] : chunk["end_idx"]]
                if chunk["part_no"] > 1:
                    chunk_lines = [content[section.start_idx], ""] + chunk_lines
                stem = slug_title(section_title + chunk["suffix"])
                write_text(chapter_dir / f"{stem}.md", render_body(chunk_lines, section_number))

                extra_lines: list[str] = []
                if chunk["parts"] > 1:
                    extra_lines.append(f"- 拆分段：第 {chunk['part_no']} 段，共 {chunk['parts']} 段")
                # 只包含当前 chunk 内的三级条款
                chunk_subs = [
                    sub for sub in section.subsections
                    if chunk["start_idx"] <= sub.start_idx < chunk["end_idx"]
                ] if section.subsections else None
                write_text(
                    chapter_dir / f"{stem}-index.md",
                    build_todo_index(
                        stem,
                        build_file_catalog_lines(
                            chapter_title,
                            f"{stem}.md",
                            section_title=section_title,
                            extra_lines=extra_lines,
                            subsections=chunk_subs,
                        ),
                    ),
                )

    write_text(out_root / f"{src_name}-index.md", build_root_index(src_name, chapters))


def main() -> None:
    parser = argparse.ArgumentParser(description="Split a markdown spec into wiki files.")
    parser.add_argument("source", help="source markdown file")
    parser.add_argument("--output-root", default="wiki", help="wiki output root directory")
    parser.add_argument(
        "--spec-name",
        default=None,
        help="wiki directory name (defaults to source file stem)",
    )
    parser.add_argument("--max-pages-per-section", type=int, default=5, help="max pages per second-level section")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    lines = source.read_text(encoding="utf-8").splitlines()
    chapters = parse_structure(lines)
    content = lines[first_content_index(lines) :]
    spec_name = args.spec_name or source.stem
    if spec_name == ".":
        output_root = Path(args.output_root).resolve()
        spec_name = source.stem
    else:
        output_root = Path(args.output_root).resolve() / spec_name

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    build_indexes(spec_name, output_root, content, chapters, args.max_pages_per_section)
    print(output_root)


if __name__ == "__main__":
    main()
