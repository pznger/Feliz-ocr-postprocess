# -*- coding: utf-8 -*-
"""Convert Chinese standard PDFs in raw/ to markdown matching existing specs."""
from __future__ import annotations

import argparse
import re
import statistics
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"

CHAPTER_RE = re.compile(r"^(\d{1,2})\s+([\u4e00-\u9fff].{1,24})$")
SECTION_RE = re.compile(r"^(\d+\.\d+)\s+([\u4e00-\u9fff].{1,30})$")
SUBSECTION_RE = re.compile(r"^(\d+\.\d+\.\d+)\s+([\u4e00-\u9fff].+)$")
APPENDIX_RE = re.compile(r"^附录\s*([A-Z])\s*(.*)$")
APPENDIX_SEC_RE = re.compile(r"^([A-Z]\.\d+)\s+([\u4e00-\u9fff].+)$")
CLAUSE_RE = re.compile(r"^(\d+\.\d+\.\d+)\s*(.*)$")
FIGURE_CAP_RE = re.compile(r"^(\d+)\s+[一—\-]")
TAIL_RE = re.compile(r"^(本规范用词说明|本标准用词说明|引用标准名录|条文说明|编制说明)$")


def normalize_clause_num(s: str) -> str:
    s = s.replace("．", ".").replace("o", "0").replace("O", "0")
    s = re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", s)
    s = re.sub(r"(\d)\s+(\d)", r"\1\2", s)
    s = re.sub(r"\s+", "", s)
    return s


def normalize_section_num(s: str) -> str:
    s = s.replace("．", ".")
    m = re.fullmatch(r"(\d+)\s*\.\s*(\d+)", s.strip())
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return s.strip()


CHAPTER_TITLE_FIXES = {
    "4 料材": "4 材料",
}


def normalize_chapter_candidate(text: str) -> str:
    text = text.strip()
    m = re.match(r"^((?:\d+\s+)+)([\u4e00-\u9fff].+)$", text)
    if m:
        num = re.sub(r"\s+", "", m.group(1))
        text = f"{num} {m.group(2).strip()}"
    return CHAPTER_TITLE_FIXES.get(text, text)


CHAPTER_NAME_SUFFIXES = (
    "总则",
    "规定",
    "加固法",
    "技术",
    "符号",
    "材料",
    "连接",
    "节点",
    "防护",
    "设计",
    "构件",
    "措施",
    "验收",
    "分析",
    "墙",
    "梁",
)


def is_plausible_chapter_name(name: str) -> bool:
    name = name.strip()
    if len(name) < 2 or len(name) > 22:
        return False
    if len(name) < 3 and name not in ("材料", "连接", "节点", "防护"):
        return False
    if any(c in name for c in "，；:：;"):
        return False
    if name.endswith(("。", "）", ")")):
        return False
    if re.search(r"\d", name):
        return False
    if any(w in name for w in ("按下式", "符合下列", "验算", "取值", "乘以", "损失值", "确定", "加荷")):
        return False
    return name.endswith(CHAPTER_NAME_SUFFIXES) or name == "术语和符号"


def try_chapter_heading(text: str, size: float, body_size: float, expected_chapter: int) -> str | None:
    _ = size
    norm = normalize_chapter_candidate(text)
    m = CHAPTER_RE.match(norm)
    if not m or not is_plausible_chapter_name(m.group(2)):
        return None
    ch_no = int(m.group(1))
    if ch_no != expected_chapter or ch_no > 25:
        return None
    return f"# {m.group(1)} {m.group(2).strip()}"


def normalize_line(text: str) -> str:
    text = text.replace("\u3000", " ").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("l=I", "l=1")
    return text


def extract_page_items(page: fitz.Page) -> list[tuple[str, float]]:
    data = page.get_text("dict")
    items: list[tuple[float, float, str, float]] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = normalize_line("".join(s["text"] for s in spans))
            if not text:
                continue
            y = line["bbox"][1]
            x = line["bbox"][0]
            size = max(s.get("size", 10) for s in spans)
            items.append((y, x, text, size))
    items.sort(key=lambda item: (round(item[0], 1), round(item[1], 1)))
    return [(text, size) for _, _, text, size in items]


def collect_chinese_label(lines: list[tuple[str, float]], start: int) -> tuple[str, float, int]:
    parts: list[str] = []
    max_size = lines[start][1] if start < len(lines) else 10.0
    i = start
    while i < len(lines):
        t, s = lines[i]
        if not t:
            i += 1
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]", t) or (len(t) <= 4 and re.fullmatch(r"[\u4e00-\u9fff]+", t)):
            parts.append(t)
            max_size = max(max_size, s)
            i += 1
        else:
            break
    return "".join(parts), max_size, i


def merge_multiline_headings(lines: list[tuple[str, float]]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    i = 0
    while i < len(lines):
        text, size = lines[i]
        spaced_sec = re.match(r"^(\d+)\s*\.\s*(\d+)\s+([\u4e00-\u9fff].+)$", text)
        if spaced_sec:
            out.append(
                (f"{spaced_sec.group(1)}.{spaced_sec.group(2)} {spaced_sec.group(3).strip()}", size)
            )
            i += 1
            continue
        ch_part = re.fullmatch(r"(\d{1,2})\s+([\u4e00-\u9fff])", text)
        if ch_part:
            extra, ls, ni = collect_chinese_label(lines, i + 1)
            name = ch_part.group(2) + extra
            if len(name) <= 12:
                out.append((f"{ch_part.group(1)} {name}", max(size, ls)))
                i = ni
                continue
        sec_m = re.fullmatch(r"(\d+)\s*\.\s*(\d+)", text)
        if sec_m:
            sec = f"{sec_m.group(1)}.{sec_m.group(2)}"
            label, ls, ni = collect_chinese_label(lines, i + 1)
            if label:
                out.append((f"{sec} {label}", max(size, ls)))
                i = ni
                continue
        if re.fullmatch(r"\d{1,2}", text) and i + 1 < len(lines):
            nxt, ns = lines[i + 1]
            if re.fullmatch(r"[\u4e00-\u9fff]", nxt):
                extra, ls, ni = collect_chinese_label(lines, i + 2)
                name = nxt + extra
                if len(name) <= 12:
                    out.append((f"{text} {name}", max(size, ns, ls)))
                    i = ni
                    continue
            if re.match(r"^[\u4e00-\u9fff]", nxt) and len(nxt) < 20:
                out.append((f"{text} {nxt}", max(size, ns)))
                i += 2
                continue
        if re.fullmatch(r"\d\.\d", text) and i + 1 < len(lines):
            nxt, ns = lines[i + 1]
            if len(nxt) <= 8 and re.match(r"^[\u4e00-\u9fff]+$", nxt):
                out.append((f"{text} {nxt}", max(size, ns)))
                i += 2
                continue
        out.append((text, size))
        i += 1
    return out


def classify(text: str, size: float, body_size: float) -> str | None:
    text = normalize_chapter_candidate(text)
    text = normalize_section_num(text) if re.fullmatch(r"\d+\s*\.\s*\d+", text.strip()) else text
    if TAIL_RE.match(text.replace(" ", "")):
        return "tail"
    if FIGURE_CAP_RE.match(text):
        return None
    large = size >= body_size + 1.2
    m = APPENDIX_RE.match(text)
    if m and large:
        rest = m.group(2).strip()
        if not rest:
            return f"# 附录{m.group(1)}"
        if len(rest) <= 30 and not re.search(r"[表。，；]|乘以|数值|折减", rest):
            return f"# 附录{m.group(1)} {rest}".strip()
    m = APPENDIX_SEC_RE.match(text)
    if m and size >= body_size + 0.5:
        return f"## {m.group(1)} {m.group(2).strip()}"
    m = SECTION_RE.match(text)
    if m:
        major = int(m.group(1).split(".")[0])
        if major == 0:
            return None
        if len(m.group(2).strip()) <= 2:
            return None
        return f"## {m.group(1)} {m.group(2).strip()}"
    m = SUBSECTION_RE.match(text)
    if m and not text.endswith(("：", ":")):
        return f"### {m.group(1)} {m.group(2).strip()}"
    return None


def convert_pdf(pdf_path: Path, out_path: Path, meta: dict, start_page: int, end_page: int) -> None:
    doc = fitz.open(pdf_path)
    sizes: list[float] = []
    for pno in range(start_page, end_page + 1):
        for _, s in extract_page_items(doc[pno - 1]):
            sizes.append(s)
    body_size = statistics.median(sizes) if sizes else 10.5

    out: list[str] = [
        "# 中华人民共和国国家标准",
        "",
        f"# {meta['title_cn']}",
        "",
        meta["title_en"],
        "",
        f"**{meta['gb']}**",
        "",
        f"施行日期：{meta['date']}",
        "",
        "---",
        "",
    ]

    seen_ch1 = False
    expected_chapter = 1
    for pno in range(start_page, end_page + 1):
        page = doc[pno - 1]
        lines = merge_multiline_headings(extract_page_items(page))
        out.append(f"<!-- {pno} -->")
        out.append("")
        stop = False
        for text, size in lines:
            if TAIL_RE.match(text.replace(" ", "")):
                stop = True
                break
            chapter = try_chapter_heading(text, size, body_size, expected_chapter)
            if chapter:
                if chapter.startswith("# 1 ") or chapter == "# 1 总则":
                    seen_ch1 = True
                out.append(chapter)
                out.append("")
                expected_chapter += 1
                continue
            heading = classify(text, size, body_size)
            if heading == "tail":
                stop = True
                break
            if heading:
                if heading.startswith("# 1 ") or heading == "# 1 总则":
                    seen_ch1 = True
                out.append(heading)
                out.append("")
                continue

            compact = normalize_clause_num(text.split()[0]) if text.split() else ""
            cm = CLAUSE_RE.match(compact) if compact else None
            if not cm:
                joined = normalize_clause_num(re.sub(r"\s+", "", text[:20]))
                m3 = re.match(r"^(\d+\.\d+\.\d+)", joined)
                if m3:
                    cm = CLAUSE_RE.match(m3.group(1))
            if cm:
                num = normalize_clause_num(cm.group(1))
                if not seen_ch1 and num.startswith("1."):
                    out.append("# 1 总则")
                    out.append("")
                    seen_ch1 = True
                    if expected_chapter == 1:
                        expected_chapter = 2
                rest = text
                for pat in [
                    r"^\d+\.\d+\.\d+\s*",
                    r"^\d+\.\s*\d+\.\s*\d+\s*",
                    r"^[\d\s\.oO]+\s*",
                ]:
                    rest = re.sub(pat, "", rest, count=1)
                rest = rest.strip()
                if rest.startswith(num):
                    rest = rest[len(num) :].strip()
                out.append(f"**{num}** {rest}".strip())
                out.append("")
                continue

            if re.fullmatch(r"\d+\.\d+\.\d+", text.replace(" ", "")):
                continue
            out.append(text)
            out.append("")
        if stop:
            break

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    out_path.write_text(text, encoding="utf-8")
    print(f"wrote {out_path} ({end_page - start_page + 1} pages, body_font={body_size:.1f})")


SPECS = [
    {
        "pdf": RAW / "GB50367-2013 混凝土结构加固设计规范.pdf",
        "out": RAW / "混凝土结构加固设计规范.md",
        "start": 17,
        "end": 184,
        "meta": {
            "title_cn": "混凝土结构加固设计规范",
            "title_en": "Code for design of strengthening concrete structure",
            "gb": "GB 50367-2013",
            "date": "2014年6月1日",
        },
    },
    {
        "pdf": RAW / "GB 50017-2017 钢结构设计标准（含条文说明）.pdf",
        "out": RAW / "钢结构设计标准.md",
        "start": 19,
        "end": 307,
        "meta": {
            "title_cn": "钢结构设计标准",
            "title_en": "Standard for design of steel structures",
            "gb": "GB 50017-2017",
            "date": "2018年7月1日",
        },
    },
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", choices=["all", "50367", "50017"], default="all")
    args = parser.parse_args()
    for item in SPECS:
        key = "50367" if "50367" in item["pdf"].name else "50017"
        if args.spec != "all" and args.spec != key:
            continue
        convert_pdf(item["pdf"], item["out"], item["meta"], item["start"], item["end"])


if __name__ == "__main__":
    main()
