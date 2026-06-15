# -*- coding: utf-8 -*-
"""Post-process raw markdown: merge broken lines, inject PDF tables, tidy formulas."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"

PAGE_RE = re.compile(r"^<!--\s*(\d+)\s*-->$")
TABLE_CAP_RE = re.compile(
    r"^(?:\*\*)?(?:续)?表\s*([\d]+(?:\s*\.\s*[\d]+)*(?:\s*[-－]\s*[\d]+)?)\s*(.*)$"
)
CLAUSE_RE = re.compile(r"^\*\*(\d+\.\d+\.\d+)\*\*")
HEADING_RE = re.compile(r"^#{1,3}\s+")
FORMULA_NO_RE = re.compile(r"^\(?(\d+\.\d+\.\d+[-－]\d+)\)?\s*$")
LIST_ITEM_RE = re.compile(r"^(\d+)\s+([\u4e00-\u9fff].+)$")
END_SENTENCE_RE = re.compile(r'[。；：！？）」』"\'％%]$')

CHAPTER_TITLES: dict[str, dict[int, str]] = {
    "50367": {
        2: "术语和符号",
        3: "基本规定",
        4: "材料",
        5: "增大截面加固法",
        6: "置换混凝土加固法",
        7: "体外预应力加固法",
        8: "外包型钢加固法",
        9: "粘贴钢板加固法",
        10: "粘贴纤维复合材加固法",
        11: "预应力碳纤维复合板加固法",
        12: "增设支点加固法",
        13: "预张紧钢丝绳网片-聚合物砂浆面层加固法",
        14: "绕丝加固法",
        15: "植筋技术",
        16: "锚栓技术",
        17: "裂缝修补技术",
    },
    "50017": {
        2: "术语和符号",
        3: "基本设计规定",
        4: "材料",
        5: "结构分析与稳定性设计",
        6: "受弯构件",
        7: "轴心受力构件",
        8: "拉弯、压弯构件",
        9: "加劲钢板剪力墙",
        10: "塑性及弯矩调幅设计",
        11: "连接",
        12: "节点",
        13: "钢管连接节点",
        14: "钢与混凝土组合梁",
        15: "钢管混凝土柱及节点",
        16: "疲劳计算及防脆断设计",
        17: "钢结构抗震性能化设计",
        18: "钢结构防护",
    },
}

SPECS = {
    "50017": {
        "md": RAW / "钢结构设计标准.md",
        "pdf": RAW / "GB 50017-2017 钢结构设计标准（含条文说明）.pdf",
        "start": 19,
        "end": 307,
    },
    "50367": {
        "md": RAW / "混凝土结构加固设计规范.md",
        "pdf": RAW / "GB50367-2013 混凝土结构加固设计规范.pdf",
        "start": 17,
        "end": 184,
    },
}


def normalize_table_num(s: str) -> str:
    s = re.sub(r"\s*\.\s*", ".", s.strip())
    s = re.sub(r"\s*[-－]\s*", "-", s)
    return s


def normalize_table_caption(line: str) -> str:
    m = TABLE_CAP_RE.match(line.strip().lstrip("*").rstrip("*").strip())
    if not m:
        return line
    num = normalize_table_num(m.group(1))
    rest = m.group(2).strip()
    prefix = "续表" if line.strip().startswith("续") else "表"
    if rest:
        return f"**{prefix}{num} {rest}**" if not line.startswith("续") else f"续表{num} {rest}".strip()
    return f"**{prefix}{num}**" if not line.startswith("续") else f"续表{num}"


def cell_html(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\n", "<br>")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def table_to_html(rows: list[list[str | None]]) -> str:
    if not rows:
        return ""
    parts = ['<table border="1" >']
    for row in rows:
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<td>{cell_html(cell)}</td>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def build_page_tables(pdf_path: Path, start_page: int, end_page: int) -> dict[int, list[str]]:
    doc = fitz.open(pdf_path)
    page_tables: dict[int, list[str]] = {}
    for pno in range(start_page, end_page + 1):
        page = doc[pno - 1]
        found = page.find_tables()
        htmls: list[str] = []
        for tab in found.tables:
            rows = tab.extract()
            if rows:
                htmls.append(table_to_html(rows))
        if htmls:
            page_tables[pno] = htmls
    return page_tables


def is_table_fragment(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if TABLE_CAP_RE.match(s.lstrip("*").rstrip("*")) or s.startswith("<table"):
        return True
    if re.fullmatch(r"[\d\.\s\-~～]+", s):
        return True
    if re.fullmatch(r"\d+", s) and len(s) <= 4:
        return True
    if s in {"柱顶为刚接", "柱顶为铰接", "柱顶为饺接"}:
        return True
    if re.match(r"^（沿屋架", s) or re.match(r"^架跨度方向", s) or re.match(r"^（垂直屋架", s):
        return True
    if len(s) <= 24 and not END_SENTENCE_RE.search(s) and not LIST_ITEM_RE.match(s):
        if re.search(r"[\u4e00-\u9fff]", s) and not CLAUSE_RE.match(s) and not HEADING_RE.match(s):
            if not re.search(r"[。；！？]", s):
                if any(k in s for k in ("温度区段", "柱顶", "跨度方向", "刚接", "铰接", "采暖", "露天", "围护")):
                    return True
                if len(s) <= 12:
                    return True
    return False


def inject_tables(lines: list[str], page_tables: dict[int, list[str]]) -> list[str]:
    out: list[str] = []
    current_page = 0
    table_idx_on_page: dict[int, int] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        pm = PAGE_RE.match(line.strip())
        if pm:
            current_page = int(pm.group(1))
            out.append(line)
            i += 1
            continue

        cap = TABLE_CAP_RE.match(line.strip().lstrip("*").rstrip("*").strip())
        is_cont = line.strip().startswith("续表")
        if cap or (is_cont and "表" in line):
            caption = normalize_table_caption(line)
            out.append(caption)
            out.append("")
            idx = table_idx_on_page.get(current_page, 0)
            tables = page_tables.get(current_page, [])
            fragments: list[str] = []
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if not nxt:
                    i += 1
                    continue
                if PAGE_RE.match(nxt):
                    break
                if TABLE_CAP_RE.match(nxt.lstrip("*").rstrip("*")) or nxt.startswith("续表"):
                    break
                if CLAUSE_RE.match(nxt) or HEADING_RE.match(nxt):
                    break
                if FORMULA_NO_RE.match(nxt):
                    break
                if LIST_ITEM_RE.match(nxt) and not is_table_fragment(nxt):
                    break
                if re.match(r"^#{1,3}\s", nxt):
                    break
                if nxt.startswith("```"):
                    break
                if nxt.startswith("<table"):
                    fragments.append(nxt)
                    i += 1
                    break
                if is_table_fragment(nxt):
                    fragments.append(nxt)
                    i += 1
                    continue
                if len(nxt) > 30 and re.search(r"[。；]", nxt):
                    break
                if re.fullmatch(r"\d+\.?\d*", nxt):
                    fragments.append(nxt)
                    i += 1
                    continue
                if len(nxt) <= 30:
                    fragments.append(nxt)
                    i += 1
                    continue
                break
            if idx < len(tables):
                out.append(tables[idx])
                table_idx_on_page[current_page] = idx + 1
            elif fragments:
                out.append(build_fragment_table(fragments))
            out.append("")
            continue

        out.append(line)
        i += 1
    return out


def can_merge(prev: str, curr: str) -> bool:
    p, c = prev.strip(), curr.strip()
    if not p or not c:
        return False
    if p == "---" or PAGE_RE.match(p) or PAGE_RE.match(c):
        return False
    if HEADING_RE.match(p) or HEADING_RE.match(c):
        return False
    if TABLE_CAP_RE.match(p) or TABLE_CAP_RE.match(c):
        return False
    if c.startswith("续表") or p.startswith("续表"):
        return False
    if p.startswith("<table") or c.startswith("<table"):
        return False
    if re.fullmatch(r"\*\*\d+\.\d+\.\d+\*\*", c):
        return False
    if CLAUSE_RE.match(c) and not re.fullmatch(r"\*\*\d+\.\d+\.\d+\*\*", p):
        return False
    if FORMULA_NO_RE.match(c) or FORMULA_NO_RE.match(p):
        return False
    if LIST_ITEM_RE.match(c):
        return False
    if c in {"式中：", "式中:"}:
        return False
    if END_SENTENCE_RE.search(p) and not re.fullmatch(r"\*\*\d+\.\d+\.\d+\*\*", p):
        return False
    if re.fullmatch(r"\d+\.?", c):
        return False
    if re.fullmatch(r"\d+\.?", p):
        if re.fullmatch(r"\*\*\d+\.\d+\.\d+\*\*", p):
            pass
        elif re.match(r"^[\u4e00-\u9fff]", c):
            pass
        else:
            return False
    if re.fullmatch(r"\d{1,3}", p) and int(p) < 400:
        if not re.match(r"^[\u4e00-\u9fff]", c):
            return False
    return True


def join_lines(a: str, b: str) -> str:
    a, b = a.rstrip(), b.lstrip()
    if re.fullmatch(r"\d+\.?", a) and re.match(r"^[\u4e00-\u9fff]", b):
        return f"{a} {b}"
    if a.endswith(("-", "—")):
        return a[:-1] + b
    if a and b and (a[-1].isascii() or (b[0].isascii() and not b[0].isdigit())):
        return a + " " + b
    return a + b


def collapse_blank_gaps(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if out and j < len(lines) and can_merge(out[-1], lines[j]):
                out[-1] = join_lines(out[-1], lines[j])
                i = j + 1
                continue
            if out and out[-1].strip() != "":
                out.append(line)
            i += 1
            continue
        out.append(line)
        i += 1
    return out


def merge_prose_lines(lines: list[str]) -> list[str]:
    lines = collapse_blank_gaps(lines)
    out: list[str] = []
    for line in lines:
        if line.strip() == "":
            if out and out[-1].strip() != "":
                out.append(line)
            continue
        if out and out[-1].strip() and can_merge(out[-1], line):
            out[-1] = join_lines(out[-1], line)
        else:
            out.append(line)
    return out


def _formula_block_end(nxt: str) -> bool:
    if not nxt:
        return False
    if nxt in {"式中：", "式中:"} or nxt.startswith("式中"):
        return True
    if CLAUSE_RE.match(nxt) or HEADING_RE.match(nxt):
        return True
    if TABLE_CAP_RE.match(nxt.lstrip("*").rstrip("*")) or nxt.startswith("续表"):
        return True
    if LIST_ITEM_RE.match(nxt):
        return True
    if PAGE_RE.match(nxt):
        return True
    return False


def _collect_formula_block(lines: list[str], start: int) -> tuple[list[str], int, str | None]:
    block: list[str] = []
    eq_no: str | None = None
    i = start
    while i < len(lines):
        nxt = lines[i].strip()
        if not nxt:
            if block:
                break
            i += 1
            continue
        fm = FORMULA_NO_RE.match(nxt)
        if fm:
            if block:
                if not eq_no:
                    eq_no = fm.group(1)
                break
            eq_no = fm.group(1)
            i += 1
            continue
        if _formula_block_end(nxt):
            break
        block.append(lines[i].strip())
        i += 1
    return block, i, eq_no


def _emit_formula(block: list[str], eq_no: str | None) -> list[str]:
    text = " ".join(re.sub(r"`+", "", ln) for ln in block).strip()
    if not text:
        return []
    text = re.sub(r"\s+", " ", text)
    if eq_no:
        return ["", f"$${text}$$ \\tag{{{eq_no}}}", ""]
    return ["", f"$${text}$$", ""]


def wrap_formula_blocks(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            i += 1
            block: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i].strip())
                i += 1
            if i < len(lines):
                i += 1
            eq_no = None
            if i < len(lines) and FORMULA_NO_RE.match(lines[i].strip()):
                eq_no = FORMULA_NO_RE.match(lines[i].strip()).group(1)
                i += 1
            out.extend(_emit_formula(block, eq_no))
            continue
        if FORMULA_NO_RE.match(stripped):
            eq_no = FORMULA_NO_RE.match(stripped).group(1)
            i += 1
            block, i, trailing_no = _collect_formula_block(lines, i)
            eq_no = eq_no or trailing_no
            out.extend(_emit_formula(block, eq_no))
            continue
        out.append(line)
        i += 1
    return out


def drop_orphan_page_numbers(lines: list[str]) -> list[str]:
    out: list[str] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if re.fullmatch(r"\d{1,3}", s) and int(s) < 400:
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if PAGE_RE.match(nxt) or nxt.startswith("<!--"):
                continue
            if out and out[-1].strip() and not PAGE_RE.match(out[-1].strip()):
                prev = out[-1].strip()
                if not prev.startswith("#") and not prev.startswith("<table"):
                    continue
        out.append(line)
    return out


def fix_50017_ocr(text: str) -> str:
    """GB 50017-specific OCR fixes (ch.13–appendices and shared symbols)."""
    subs = [
        ("析了架", "桁架"),
        ("析了", "桁架"),
        ("椅架", "桁架"),
        ("和架", "桁架"),
        ("珩架", "桁架"),
        ("衍架", "桁架"),
        ("吊车衍架", "吊车桁架"),
        ("制动衍架", "制动桁架"),
        ("伸臂椅架", "伸臂桁架"),
        ("环带俯架", "环带桁架"),
        ("环带析了架", "环带桁架"),
        ("伸臂析了架", "伸臂桁架"),
        ("100€~", r"$100\varepsilon_k$"),
        ("100€", r"$100\varepsilon_k$"),
        ("40缸", r"$40\varepsilon_k$"),
        ("15ευ", r"$15\varepsilon_k$"),
        ("Slil", r"\\sin"),
        ("Slinc", r"\\sin"),
        ("Slill", r"\\sin"),
        ("CN/mm2)", "(N/mm²)"),
        ("CN/mm2;", "(N/mm²)；"),
        ("CN/mm2 ", "(N/mm²) "),
        ("CN/mm2。", "(N/mm²)。"),
        ("CN/mm勺", "(N/mm²)"),
        ("CN •mm", "N·mm"),
        ("CN • mm", "N·mm"),
        ("CN/mm", "N/mm"),
        ("CN)", "N)"),
        ("伊bx", r"$\varphi_{bx}$"),
        ("伊by", r"$\varphi_{by}$"),
        ("伊bs", r"$\varphi_{bs}$"),
        ("伊σ", r"$\varphi_\sigma$"),
        ("伊s", r"$\varphi_s$"),
        ("伊b", r"$\varphi_b$"),
        ("Q二三", r"$Q \geq$"),
        ("三三", "≤"),
        ("三二", "≤"),
        ("l. Oo", "1.0"),
        ("l. O", "1.0"),
        ("==l.", "= 1."),
        ("γt==l.O", r"$\gamma_t = 1.0$"),
        ("今τ", r"$\Delta\tau$"),
        ("Aτ", r"$\Delta\tau$"),
        ("Aσ", r"$\Delta\sigma$"),
        ("!:::Di", r"$\Delta\sigma_i$"),
        ("!:::oe", r"$\Delta\sigma_e$"),
        ("f:::oe", r"$\Delta\sigma_e$"),
        ("~N", r"$\Delta N$"),
        ("~Te", r"$\Delta\tau_e$"),
        ("~τ", r"$\Delta\tau$"),
        ("~σ", r"$\Delta\sigma$"),
        ("[&;L]", r"$[\Delta\sigma_L]$"),
        ("[&-L]", r"$[\Delta\sigma_L]$"),
        ("[&JL]", r"$[\Delta\sigma_L]$"),
        ("[&r]s", r"$[\Delta\sigma]_{5\times10^6}$"),
        ("［~σ］", r"$[\Delta\sigma]$"),
        ("［~τ］", r"$[\Delta\tau]$"),
        ("［~rL]", r"$[\Delta\tau_L]$"),
        ("1Jov", r"$\eta_{ov}$"),
        ("加劲胁", "加劲板"),
        ("抗风扬架", "抗风桁架"),
        ("子动", "手动"),
        ("LSO", "L50"),
        ("sb48", "φ48"),
        ("粥。", "φ89"),
        ("佛钉", "铆钉"),
        ("j了", r"$f_w^t$"),
        ("句一一", r"$\Delta\sigma$ ——"),
        ("比＝", r"$\gamma_t =$"),
        ("（子）", r"$(25/t)$"),
        ("（引", r"$(30/d)$"),
        ("03.2.4", "13.2.4"),
        ("Zg", "Z9"),
        ("ZlO", "Z10"),
        ("Zll", "Z11"),
        ("Zl3", "Z13"),
    ]
    for old, new in subs:
        text = text.replace(old, new)
    text = re.sub(r"(-?\d+\.?\d*)\s*《\s*([^《]+)\s*《\s*([^《\n]+)", r"\1 \\leq \2 \\leq \3", text)
    text = re.sub(r"(\d+%)\s*《\s*([^《]+)\s*《\s*(\d+%)", r"\1 \\leq \2 \\leq \3", text)
    text = re.sub(r"<td>12</td><td>2\.00× 1016", "<td>J2</td><td>2.00×10^16", text)
    return text


def normalize_spacing(text: str) -> str:
    text = re.sub(r"表\s*(\d+)\s*\.\s*(\d+)", r"表\1.\2", text)
    text = re.sub(r"表\s*(\d+)\s*\.\s*(\d+)\s*\.\s*(\d+)", r"表\1.\2.\3", text)
    text = re.sub(r"(\d+)\s*\.\s*(\d+)\s*\.\s*(\d+)", r"\1.\2.\3", text)
    text = re.sub(r"(\d+)\s+(\d+)(?=\s*[%％])", r"\1.\2", text)
    text = re.sub(r"\bG\s*B\s*/?\s*T\s*", "GB/T ", text)
    text = re.sub(r"\bG\s*B\s+(\d)", r"GB \1", text)
    text = re.sub(r"\bJ\s*G\s*J\s*", "JGJ ", text)
    text = re.sub(r"(\d)\s+(\d)(?=\s*[Kk])", r"\1\2", text)
    text = text.replace("饺接", "铰接").replace("锵钉", "铆钉")
    text = text.replace("im portant", "important").replace("w ith", "with")
    text = re.sub(
        r"\*\*3\.3\.5\*\*\s*中数字可予以当有充分依据或可靠措施时，表增减。",
        "4 当有充分依据或可靠措施时，表3.3.5中数值可予以增减。",
        text,
    )
    text = fix_50017_ocr(text)
    return text


TABLE_16_2_1_1 = (
    '<table border="1">'
    "<tr>"
    '<td rowspan="2">构件与连接类别</td>'
    '<td colspan="2">构件与连接相关系数</td>'
    '<td rowspan="2">循环次数 $n$ 为 $2\\times10^6$ 次的容许正应力幅 $[\\Delta\\sigma]_{2\\times10^6}$（N/mm²）</td>'
    '<td rowspan="2">循环次数 $n$ 为 $5\\times10^6$ 次的容许正应力幅 $[\\Delta\\sigma]_{5\\times10^6}$（N/mm²）</td>'
    '<td rowspan="2">疲劳截止限 $[\\Delta\\sigma_L]_{1\\times10^8}$（N/mm²）</td>'
    "</tr>"
    "<tr><td>$C_z$</td><td>$\\beta_z$</td></tr>"
    "<tr><td>Z1</td><td>$1920\\times10^{12}$</td><td>4</td><td>176</td><td>140</td><td>85</td></tr>"
    "<tr><td>Z2</td><td>$861\\times10^{12}$</td><td>4</td><td>144</td><td>115</td><td>70</td></tr>"
    "<tr><td>Z3</td><td>$3.91\\times10^{12}$</td><td>3</td><td>125</td><td>92</td><td>51</td></tr>"
    "<tr><td>Z4</td><td>$2.81\\times10^{12}$</td><td>3</td><td>112</td><td>83</td><td>46</td></tr>"
    "<tr><td>Z5</td><td>$2.00\\times10^{12}$</td><td>3</td><td>100</td><td>74</td><td>41</td></tr>"
    "<tr><td>Z6</td><td>$1.46\\times10^{12}$</td><td>3</td><td>90</td><td>66</td><td>36</td></tr>"
    "<tr><td>Z7</td><td>$1.02\\times10^{12}$</td><td>3</td><td>80</td><td>59</td><td>32</td></tr>"
    "<tr><td>Z8</td><td>$0.72\\times10^{12}$</td><td>3</td><td>71</td><td>52</td><td>29</td></tr>"
    "<tr><td>Z9</td><td>$0.50\\times10^{12}$</td><td>3</td><td>63</td><td>46</td><td>25</td></tr>"
    "<tr><td>Z10</td><td>$0.35\\times10^{12}$</td><td>3</td><td>56</td><td>41</td><td>23</td></tr>"
    "<tr><td>Z11</td><td>$0.25\\times10^{12}$</td><td>3</td><td>50</td><td>37</td><td>20</td></tr>"
    "<tr><td>Z12</td><td>$0.18\\times10^{12}$</td><td>3</td><td>45</td><td>33</td><td>18</td></tr>"
    "<tr><td>Z13</td><td>$0.13\\times10^{12}$</td><td>3</td><td>40</td><td>29</td><td>16</td></tr>"
    "<tr><td>Z14</td><td>$0.09\\times10^{12}$</td><td>3</td><td>36</td><td>26</td><td>14</td></tr>"
    "</table>"
)

TABLE_16_2_1_2 = (
    '<table border="1">'
    "<tr>"
    '<td rowspan="2">构件与连接类别</td>'
    '<td colspan="2">构件与连接的相关系数</td>'
    '<td rowspan="2">循环次数 $n$ 为 $2\\times10^6$ 次的容许剪应力幅 $[\\Delta\\tau]_{2\\times10^6}$（N/mm²）</td>'
    '<td rowspan="2">疲劳截止限 $[\\Delta\\tau_L]_{1\\times10^8}$（N/mm²）</td>'
    "</tr>"
    "<tr><td>$C_J$</td><td>$\\beta_J$</td></tr>"
    "<tr><td>J1</td><td>$4.10\\times10^{11}$</td><td>3</td><td>59</td><td>16</td></tr>"
    "<tr><td>J2</td><td>$2.00\\times10^{16}$</td><td>5</td><td>100</td><td>46</td></tr>"
    "<tr><td>J3</td><td>$8.61\\times10^{21}$</td><td>8</td><td>90</td><td>55</td></tr>"
    "</table>"
)


def apply_manual_tables(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        cap = line.strip().lstrip("*").rstrip("*")
        if cap.startswith("表16.2.1-1"):
            out.append("**表16.2.1-1 正应力幅的疲劳计算参数**")
            out.append("")
            out.append(TABLE_16_2_1_1)
            out.append("")
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                if s.startswith("**表16.2.1-2") or s.startswith("**16.2.2"):
                    break
                if s.startswith("<table"):
                    i += 1
                    continue
                if s == "":
                    i += 1
                    continue
                break
            continue
        if cap.startswith("表16.2.1-2"):
            out.append("**表16.2.1-2 剪应力幅的疲劳计算参数**")
            out.append("")
            out.append(TABLE_16_2_1_2)
            out.append("")
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                if s.startswith("**16.2.2"):
                    break
                if s.startswith("<table"):
                    i += 1
                    continue
                if s == "":
                    i += 1
                    continue
                break
            continue
        out.append(line)
        i += 1
    return out


def apply_50017_formula_fixes(lines: list[str]) -> list[str]:
    """Replace known broken formula blocks with corrected LaTeX."""
    fixes: dict[str, str] = {
        "13.1.4": r"M = \Delta N \cdot e",
        "13.2.1": r"-0.55 \leq e/D \text{（或 } e/h \text{）} \leq 0.25",
        "13.2.4-1": r"l_p \geq \sqrt{b_p(b_p - b_1)}",
        "13.2.4-2": r"l_p \geq 1.5(b_1 + a + h_1/\sin\theta_1)",
        "13.2.4-3": r"l_p \geq 2h_1 + b_1 + b_2",
        "16.2.1-1": r"\Delta\sigma < \gamma_t [\Delta\sigma_L]_{1\times10^8}",
        "16.2.1-2": r"\Delta\sigma = \sigma_{\max} - \sigma_{\min}",
        "16.2.1-3": r"\Delta\sigma = \sigma_{\max} - 0.7\sigma_{\min}",
        "16.2.1-4": r"\Delta\tau < [\Delta\tau_L]_{1\times10^8}",
        "16.2.1-5": r"\Delta\tau = \tau_{\max} - \tau_{\min}",
        "16.2.1-6": r"\Delta\tau = \tau_{\max} - 0.7\tau_{\min}",
        "16.2.1-7": r"\gamma_t = \left(\frac{25}{t}\right)^{0.25}",
        "16.2.1-8": r"\gamma_t = \left(\frac{30}{d}\right)^{0.25}",
        "16.2.2-1": r"\Delta\sigma \leq [\Delta\sigma]",
        "16.2.2-2": r"[\Delta\sigma] = \left(\frac{C_z}{n}\right)^{1/\beta_z}",
        "16.2.2-3": r"[\Delta\sigma] = [[\Delta\sigma]_{5\times10^6}]^{0.8}",
        "16.2.2-5": r"\Delta\tau \leq [\Delta\tau]",
        "16.2.2-6": r"[\Delta\tau] = \left(\frac{C_J}{n}\right)^{1/\beta_J}",
    }
    out: list[str] = []
    for line in lines:
        m = re.match(r"^\$\$(.+)\$\$\\tag\{([^}]+)\}$", line.strip())
        if m:
            tag = m.group(2)
            if tag in fixes:
                out.append(f"$${fixes[tag]}$$ \\tag{{{tag}}}")
                continue
        out.append(line)
    return out


def build_fragment_table(lines: list[str]) -> str:
    for ln in lines:
        s = ln.strip()
        if s.startswith("<table"):
            return s
    rows: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("<table"):
            continue
        rows.append(f"<tr><td>{s}</td></tr>")
    if not rows:
        return ""
    return '<table border="1" >' + "".join(rows) + "</table>"


CHAPTER_LINE_RE = re.compile(r"^(\d{1,2})\s+([\u4e00-\u9fff].{1,22})$")
FALSE_SECTION_RE = re.compile(r"^##\s+(0\.\d+|[01]\.\d{2})\s+")
VALID_SECTION_RE = re.compile(r"^##\s+(\d+)\.(\d+)\s+")


def fix_chapter_headings(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[int] = set()
    for line in lines:
        s = line.strip()
        if s.startswith("#"):
            m = re.match(r"^#\s+(\d{1,2})\s+", s)
            if m:
                seen.add(int(m.group(1)))
            out.append(line)
            continue
        m = CHAPTER_LINE_RE.match(s)
        if m:
            ch = int(m.group(1))
            name = m.group(2).strip()
            if ch not in seen and ch <= 20 and len(name) >= 2 and "力口" not in name:
                if (
                    name.endswith(("则", "法", "术", "料", "号", "录", "技术"))
                    or "术语" in name
                    or name in {"基本规定", "材料"}
                ) and name != "设计规定":
                    out.append(f"# {ch} {name}")
                    out.append("")
                    seen.add(ch)
                    continue
        out.append(line)
    return out


CHAPTER_OCR_FIXES = [
    (r"基本规定\s*3\.1", "# 3 基本规定\n\n## 3.1 一般规定"),
    (r"3\s+一般规定\s*3\.1", "# 3 基本规定\n\n## 3.1 一般规定"),
    (r"^基本规定\s*$", "# 3 基本规定"),
    (r"1\s+0\s+粘贴纤维复合材加固法", "# 10 粘贴纤维复合材加固法"),
    (r"1\s+1\s+预应力碳纤维复合板加固法", "# 11 预应力碳纤维复合板加固法"),
    (r"1\s+2\s+增设支点加固法", "# 12 增设支点加固法"),
    (r"1\s+3\s+预张紧钢丝绳网片[-－]?聚合物砂浆面层加固法", "# 13 预张紧钢丝绳网片-聚合物砂浆面层加固法"),
    (r"1\s+4\s+绕丝加固法", "# 14 绕丝加固法"),
    (r"1\s+5\s+植筋技术", "# 15 植筋技术"),
    (r"1\s+6\s+锚栓技术", "# 16 锚栓技术"),
    (r"1\s+7\s+裂缝修补技术", "# 17 裂缝修补技术"),
]


def clean_chapter_titles(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        m = re.match(r"^(#\s+\d{1,2})\s+(.+)$", line.strip())
        if m:
            prefix, title = m.group(1), m.group(2).strip()
            title = re.sub(r"设计规定.*$", "", title).strip()
            title = re.sub(r"\s*\d\s*[\d\.].*$", "", title).strip()
            title = re.sub(r"(-聚合物砂浆面层加固法)+$", "-聚合物砂浆面层加固法", title)
            line = f"{prefix} {title}"
        out.append(line)
    return out


def fix_ocr_chapter_markers(lines: list[str]) -> list[str]:
    text = "\n".join(lines)
    for pat, repl in CHAPTER_OCR_FIXES:
        text = re.sub(pat, repl, text, count=1)
    return text.splitlines()


def polish_chapter_headings(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen_ch: set[int] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s in {"基本规定", "术语和符号"} and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if s == "基本规定" and ("3.1" in nxt or nxt.startswith("**3.1")):
                if 3 not in seen_ch:
                    out.append("# 3 基本规定")
                    out.append("")
                    seen_ch.add(3)
                i += 1
                continue
            if s == "术语和符号" and 2 not in seen_ch:
                out.append("# 2 术语和符号")
                out.append("")
                seen_ch.add(2)
                i += 1
                continue
        m = re.match(r"^#\s+(\d{1,2})\s+(.+)$", s)
        if m:
            ch = int(m.group(1))
            title = m.group(2).strip()
            if title in {"设计规定", "一般规定"}:
                i += 1
                continue
            if title == "术语":
                line = f"# {ch} 术语和符号"
            if ch in seen_ch:
                i += 1
                continue
            seen_ch.add(ch)
        out.append(line)
        i += 1
    return out


def dedupe_chapter_headings(lines: list[str]) -> list[str]:
    seen: dict[int, int] = {}
    out: list[str] = []
    for line in lines:
        m = re.match(r"^#\s+(\d{1,2})\s+(.+)$", line.strip())
        if m:
            ch = int(m.group(1))
            title = m.group(2).strip()
            if ch in seen:
                prev_idx = seen[ch]
                prev_title = out[prev_idx].strip().split(maxsplit=2)[-1] if prev_idx < len(out) else ""
                if len(title) > len(prev_title):
                    out[prev_idx] = f"# {ch} {title}"
                continue
            seen[ch] = len(out)
        out.append(line)
    return out


def infer_chapters_from_sections(lines: list[str], spec_key: str) -> list[str]:
    titles = CHAPTER_TITLES.get(spec_key, {})
    existing = {
        int(m.group(1))
        for ln in lines
        if (m := re.match(r"^#\s+(\d{1,2})\s+", ln.strip()))
    }
    out: list[str] = []
    inserted: set[int] = set()
    for line in lines:
        m = VALID_SECTION_RE.match(line.strip())
        if m:
            ch = int(m.group(1))
            if ch not in existing and ch not in inserted and ch >= 2:
                title = titles.get(ch)
                if title:
                    out.append(f"# {ch} {title}")
                    out.append("")
                    inserted.add(ch)
        out.append(line)
    return out


def demote_false_sections(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if FALSE_SECTION_RE.match(line.strip()):
            out.append(line.strip().lstrip("#").strip())
        else:
            out.append(line)
    return out


def clean_lines(lines: list[str]) -> list[str]:
    lines = [normalize_spacing(ln) for ln in lines]
    lines = [normalize_table_caption(ln) if TABLE_CAP_RE.match(ln.strip().lstrip("*").rstrip("*")) else ln for ln in lines]
    return lines


def process_file(md_path: Path, pdf_path: Path, start_page: int, end_page: int, spec_key: str) -> None:
    print(f"building tables from {pdf_path.name}...")
    page_tables = build_page_tables(pdf_path, start_page, end_page)
    print(f"  pages with tables: {len(page_tables)}")

    lines = md_path.read_text(encoding="utf-8").splitlines()
    lines = clean_lines(lines)
    lines = demote_false_sections(lines)
    lines = fix_chapter_headings(lines)
    lines = dedupe_chapter_headings(lines)
    lines = infer_chapters_from_sections(lines, spec_key)
    lines = dedupe_chapter_headings(lines)
    lines = polish_chapter_headings(lines)
    if spec_key == "50367":
        lines = fix_ocr_chapter_markers(lines)
        lines = infer_chapters_from_sections(lines, spec_key)
        lines = polish_chapter_headings(lines)
    lines = clean_chapter_titles(lines)
    lines = inject_tables(lines, page_tables)
    if spec_key == "50017":
        lines = apply_manual_tables(lines)
    lines = merge_prose_lines(lines)
    lines = merge_prose_lines(lines)
    lines = wrap_formula_blocks(lines)
    if spec_key == "50017":
        lines = apply_50017_formula_fixes(lines)
    lines = drop_orphan_page_numbers(lines)

    compact: list[str] = []
    prev_blank = False
    for line in lines:
        blank = line.strip() == ""
        if blank and prev_blank:
            continue
        compact.append(line)
        prev_blank = blank

    md_path.write_text("\n".join(compact).strip() + "\n", encoding="utf-8")
    print(f"wrote {md_path}")


def process_formulas_only(md_path: Path) -> None:
    lines = md_path.read_text(encoding="utf-8").splitlines()
    lines = [normalize_spacing(ln) for ln in lines]
    lines = merge_prose_lines(lines)
    lines = wrap_formula_blocks(lines)
    lines = drop_orphan_page_numbers(lines)
    compact: list[str] = []
    prev_blank = False
    for line in lines:
        blank = line.strip() == ""
        if blank and prev_blank:
            continue
        compact.append(line)
        prev_blank = blank
    md_path.write_text("\n".join(compact).strip() + "\n", encoding="utf-8")
    print(f"wrote formulas {md_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", choices=["all", "50367", "50017"], default="all")
    parser.add_argument(
        "--formulas-only",
        action="store_true",
        help="only merge prose and convert formulas; skip PDF table injection",
    )
    args = parser.parse_args()
    for key, cfg in SPECS.items():
        if args.spec != "all" and args.spec != key:
            continue
        if args.formulas_only:
            process_formulas_only(cfg["md"])
        else:
            process_file(cfg["md"], cfg["pdf"], cfg["start"], cfg["end"], key)


if __name__ == "__main__":
    main()
