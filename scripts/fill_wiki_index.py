#!/usr/bin/env python3
"""Fill wiki -index.md (catalog + per-clause summaries) and body .md related links."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
KB = ROOT / "知识库以及文档"

CLAUSE_BOLD_RE = re.compile(r"^\*\*(\d+(?:\.\d+)+)\*\*\s*(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
CLAUSE_PLAIN_RE = re.compile(r"^(\d+(?:\.\d+)+)\s+([\u4e00-\u9fff].+)$")
APPENDIX_CLAUSE_RE = re.compile(r"^([A-Z](?:\.\d+)+)\s*([\u4e00-\u9fff].*)$")
LIST_ITEM_RE = re.compile(r"^\d+\s+[\u4e00-\u9fff]")
SECTION_RE = re.compile(r"^(\d+\.\d+)(?=\s|[^\d.])\s*(.*)$")
CHAPTER_RE = re.compile(r"^(\d+)(?=\s|[^\d.])\s*(.+)$")
APPENDIX_SEC_RE = re.compile(r"^([A-Z]\.\d+)(?=\s|[^\d.])\s*(.*)$")
REF_CHAPTER_RE = re.compile(r"第(\d+)章")
REF_SECTION_RE = re.compile(r"第(\d+\.\d+)(?:\.\d+)?条")
REF_INLINE_RE = re.compile(r"本(?:规范|标准)第(\d+(?:\.\d+)?)")


def catalog_title(text: str) -> str:
    text = text.strip().replace("\u3000", " ")
    text = text.replace("．", ".")
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(\d+\.\d+\.\d+)(?=[^\d.\s])", r"\1 ", text)
    text = re.sub(r"^(\d+\.\d+)(?=[^\d.\s])", r"\1 ", text)
    text = re.sub(r"^(\d+)(?=[\u4e00-\u9fff])", r"\1 ", text)
    text = re.sub(r"^([A-Z]\.\d+\.\d+)(?=[^\d.\s])", r"\1 ", text)
    text = re.sub(r"^([A-Z]\.\d+)(?=[^\d.\s])", r"\1 ", text)
    return text.replace(" ", "")


def normalize_body_text(parts: list[str], max_len: int = 200) -> str:
    text = "".join(parts)
    placeholders: list[str] = []

    def _stash(match: "re.Match[str]") -> str:
        placeholders.append(match.group(0))
        return f"\x00MATH{len(placeholders) - 1}\x00"

    # 先把公式段（块级 $$...$$ 与行内 $...$）换成占位符，避免后面的
    # `\s+ -> ""` 把公式里 LaTeX 命令之间的关键空白也压掉（例如
    # `$$V\leq R$$` 会被破坏成 `$$V\leqR$$`），也避免相邻两段块级公式
    # 拼成 `$$$$` 这种渲染失败串。
    text = re.sub(r"\$\$.+?\$\$", _stash, text, flags=re.DOTALL)
    text = re.sub(r"\$[^$\n]+?\$", _stash, text)

    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"\s+", "", text)
    text = text.replace("；", "，").strip("，。；:：")

    def _restore(match: "re.Match[str]") -> str:
        return placeholders[int(match.group(1))]

    text = re.sub(r"\x00MATH(\d+)\x00", _restore, text)
    text = re.sub(r"(\$\$)(\$\$)", r"\1 \2", text)

    if len(text) > max_len:
        text = _safe_truncate(text, max_len).rstrip("，、") + "…"
    return text


def _safe_truncate(text: str, max_len: int) -> str:
    """在 max_len 处截断文本，但避免把 inline `$...$` 公式切掉一半。

    Markdown 渲染（Typora 等）遇到孤立 `$` 会拒绝渲染整行公式，
    所以摘要里出现 `$ξ_{b` / `$\\beta_{c` 之类残缺公式时整段都会失败。
    本函数会回退到上一个完整公式之前的位置，必要时回退到 max_len 之前。
    """
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    # 数一下 `$` 数量（忽略转义 \$）：奇数代表切到了未闭合的公式
    dollar_count = len(re.findall(r"(?<!\\)\$", truncated))
    if dollar_count % 2 == 0:
        return truncated
    # 切掉了一半公式：回退到最后一个 `$` 之前
    last = truncated.rfind("$")
    if last <= 0:
        return truncated
    return truncated[:last].rstrip()


def is_noise_line(text: str) -> bool:
    if not text or text in {"浏览专", "住房城"}:
        return True
    if re.fullmatch(r"[\d\s]+", text):
        return True
    if len(text) <= 2 and not re.search(r"[\u4e00-\u9fff]", text):
        return True
    return False


def is_noise_entry(num: str, text: str) -> bool:
    if not text or len(text) < 3:
        return True
    if re.search(r"[\u4e00-\u9fff]", text) and len(re.findall(r"[\u4e00-\u9fff]", text)) < 2:
        return True
    if re.fullmatch(r"[\d.\-\s图]+", text):
        return True
    if re.search(r"^[\d.\-]+图", text):
        return True
    parts = num.split(".")
    if len(parts) >= 3 and parts[-1].isdigit() and int(parts[-1]) > 30:
        return True
    return False


def chapter_sort_key(name: str) -> tuple:
    if name.startswith("附录"):
        letter = name.replace("附录", "")[:1]
        return (2, letter, name)
    m = re.match(r"^(\d+)", name)
    if m:
        return (0, int(m.group(1)), name)
    return (1, 0, name)


def parse_heading_clause(title: str) -> tuple[str, str] | None:
    title = title.strip().replace("**", "")
    m = re.match(r"^(\d+(?:\.\d+)+)\s*(.*)$", title)
    if m:
        return m.group(1), m.group(2).strip()
    m = APPENDIX_CLAUSE_RE.match(title)
    if m:
        return m.group(1), m.group(2).strip()
    return None


def collect_clause_text(lines: list[str], start: int, max_len: int = 180) -> tuple[str, int]:
    parts: list[str] = []
    list_hints: list[str] = []
    i = start
    while i < len(lines):
        raw = lines[i].strip()
        if not raw:
            i += 1
            if parts and not list_hints:
                break
            continue
        if raw.startswith("#") or CLAUSE_BOLD_RE.match(raw):
            break
        if HEADING_RE.match(raw):
            parsed = parse_heading_clause(HEADING_RE.match(raw).group(2))
            if parsed:
                break
        if CLAUSE_PLAIN_RE.match(raw) or APPENDIX_CLAUSE_RE.match(raw):
            break
        if LIST_ITEM_RE.match(raw):
            item = re.sub(r"^\d+\s+", "", raw)
            if len(item) >= 4:
                list_hints.append(item[:40])
            i += 1
            continue
        if is_noise_line(raw):
            i += 1
            continue
        parts.append(raw)
        i += 1
        if len("".join(parts)) >= max_len:
            break
    text = normalize_body_text(parts, max_len)
    if list_hints:
        hint = "；".join(list_hints[:4])
        text = f"{text}；{hint}" if text else hint
    return text, i


def expected_prefix(section_title: str) -> str | None:
    title = section_title.replace(" ", "")
    m = re.match(r"^(\d+\.\d+)", title)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+)", title)
    if m:
        return m.group(1)
    m = re.match(r"^(附录[A-Z])", title)
    if m:
        return m.group(1)
    m = re.match(r"^([A-Z]\.\d+)", title)
    if m:
        return m.group(1)
    return None


def clause_matches_prefix(num: str, prefix: str | None) -> bool:
    if not prefix:
        return True
    if prefix.startswith("附录"):
        letter = prefix.replace("附录", "")
        return num.startswith(letter)
    return num.startswith(prefix + ".") or num == prefix


def extract_entries(lines: list[str], prefix: str | None = None) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        if not raw or raw.startswith("<!--"):
            i += 1
            continue

        num = ""
        lead = ""

        m = CLAUSE_BOLD_RE.match(raw)
        if m:
            num, lead = m.group(1), m.group(2).strip()
            i += 1
            if not lead:
                while i < len(lines):
                    nxt = lines[i].strip()
                    if not nxt:
                        i += 1
                        continue
                    if nxt.startswith("#") or CLAUSE_BOLD_RE.match(nxt):
                        break
                    if LIST_ITEM_RE.match(nxt):
                        break
                    lead = nxt
                    i += 1
                    break
        else:
            hm = HEADING_RE.match(raw)
            if hm and len(hm.group(1)) >= 3:
                parsed = parse_heading_clause(hm.group(2))
                if parsed:
                    num, lead = parsed
                    i += 1
            else:
                pm = CLAUSE_PLAIN_RE.match(raw)
                if pm:
                    num, lead = pm.group(1), pm.group(2).strip()
                    i += 1
                else:
                    am = APPENDIX_CLAUSE_RE.match(raw)
                    if am and "." in am.group(1):
                        num, lead = am.group(1), am.group(2).strip()
                        i += 1

        if not num or num in seen:
            if not num:
                i += 1
            continue

        parts = [lead] if lead else []
        extra, ni = collect_clause_text(lines, i, max_len=350)
        if extra:
            parts.append(extra)
        text = normalize_body_text(parts, 220)
        if not text and lead:
            text = normalize_body_text([lead], 100)
        if is_noise_entry(num, text or lead):
            i = ni if ni > i else i + 1
            continue
        if not clause_matches_prefix(num, prefix):
            i = ni if ni > i else i + 1
            continue
        entries.append((num, text or lead or "相关技术规定"))
        seen.add(num)
        i = ni if ni > i else i + 1

    return entries


def chapter_has_sections(ch_dir: Path) -> bool:
    indexes = sorted(ch_dir.glob("*-index.md"))
    if len(indexes) != 1:
        return True
    body = indexes[0].with_name(indexes[0].stem.replace("-index", "") + ".md")
    if not body.exists():
        return True
    for line in body.read_text(encoding="utf-8").splitlines():
        hm = HEADING_RE.match(line.strip())
        if not hm or len(hm.group(1)) != 2:
            continue
        title = hm.group(2).strip()
        if SECTION_RE.match(title) or APPENDIX_SEC_RE.match(title):
            return True
    return False


def section_title_from_body(lines: list[str], stem: str) -> str:
    for raw in lines:
        raw = raw.strip()
        if not raw or raw.startswith("<!--"):
            continue
        hm = HEADING_RE.match(raw)
        if hm:
            title = hm.group(2).strip()
            if SECTION_RE.match(title) or CHAPTER_RE.match(title) or title.startswith("附录"):
                return catalog_title(title)
        if SECTION_RE.match(raw.replace("**", "")):
            return catalog_title(raw.replace("**", ""))
    return catalog_title(stem)


THEME_CHECKS: list[tuple[tuple[str, ...], str]] = [
    (("应包括", "下列内容", "应包含"), "设计内容与工作范围"),
    (("适用于", "本方法适用", "本标准适用"), "适用对象与条件"),
    (("不应低于", "不应小于", "不得低于"), "参数下限要求"),
    (("不应", "严禁", "不得", "不宜超过"), "禁止或限制性规定"),
    (("应采用", "应进行", "应计算", "应验算"), "应执行的验算与分析方法"),
    (("构造", "连接", "锚固", "箍筋"), "构造与连接措施"),
    (("极限状态",), "极限状态设计原则"),
    (("安全等级", "使用年限"), "安全等级与使用年限"),
    (("荷载", "组合", "动力荷载", "荷载效应"), "荷载取值与组合"),
    (("强度", "承载力", "稳定性", "稳定"), "强度与稳定性验算"),
    (("裂缝", "挠度", "变形"), "正常使用极限状态控制"),
    (("抗震", "抗震等级", "抗震设防"), "抗震设计要求"),
    (("混凝土", "钢材", "胶粘剂", "纤维", "钢丝绳"), "材料性能与选用"),
    (("检验", "试验", "检测", "验收"), "检验试验与验收"),
    (("公式", "按下式", "计算应符合"), "计算公式与假定"),
    (("鉴定", "检测"), "检测鉴定要求"),
    (("加固",), "加固设计与施工要求"),
    (("防腐", "防火", "防护", "耐久"), "防护与耐久性要求"),
    (("疲劳",), "疲劳验算规定"),
    (("焊接", "螺栓", "焊缝", "销轴"), "连接设计规定"),
    (("节点", "柱脚", "支座"), "节点与支座设计"),
    (("预应力",), "预应力设计与张拉"),
    (("卸荷", "卸除", "临时"), "施工阶段与临时安全措施"),
]


def extract_themes(all_text: str) -> list[str]:
    themes: list[str] = []
    for keywords, label in THEME_CHECKS:
        if any(k in all_text for k in keywords) and label not in themes:
            themes.append(label)
    return themes


def section_topic_name(section_title: str) -> str:
    name = section_title
    for token in (
        "设计规定",
        "一般规定",
        "构造要求",
        "构造规定",
        "加固计算",
        "设计计算",
        "锚固计算",
        "抗火设计",
        "隔热",
    ):
        name = name.replace(token, "")
    name = re.sub(r"^[\d.]+", "", name)
    if not name or len(name) < 2:
        return "本节"
    return name


def _list_topics(text: str) -> list[str]:
    topics: list[str] = []
    checks = [
        (("方案", "选型", "布置"), "结构方案与布置"),
        (("材料", "截面"), "材料与截面选择"),
        (("作用", "分析", "效应"), "作用效应分析"),
        (("验算", "极限状态"), "极限状态验算"),
        (("构造", "连接", "节点"), "构造与连接"),
        (("制作", "安装", "运输", "防腐", "防火"), "制作安装及防护"),
        (("竖向", "水平荷载", "传递"), "荷载传递途径"),
        (("刚度", "稳定", "冗余"), "刚度稳定与体系冗余"),
        (("强度", "稳定", "承载力"), "强度与稳定性"),
        (("疲劳",), "疲劳计算"),
        (("动力荷载",), "动力荷载取值"),
        (("焊接", "螺栓", "焊缝"), "连接设计"),
        (("抗震",), "抗震设计"),
        (("裂缝", "修补"), "裂缝修补"),
        (("植筋", "锚栓", "锚固"), "锚固与连接"),
        (("预应力",), "预应力设计"),
        (("卸荷", "卸除"), "荷载卸除与施工措施"),
    ]
    for keys, label in checks:
        if any(k in text for k in keys) and label not in topics:
            topics.append(label)
    return topics


def introduce_clause(num: str, text: str) -> str:
    """One-sentence introduction for a third-level clause (not copying catalog)."""
    t = text.strip()
    if not t or len(t) < 4:
        return "阐明相关技术规定与执行要求。"

    if "预应力钢结构" in t or ("预应力" in t and ("施工阶段" in t or "使用阶段" in t)):
        return "规定预应力钢结构须按施工阶段和使用阶段各工况分别进行设计验算。"

    if "起重机" in t:
        return "规定吊车梁疲劳与挠度计算时起重机荷载的取值与确定方法。"

    topics = _list_topics(t)
    if topics and ("；" in t or "应包括" in t or "包括" in t):
        return f"规定须涵盖{'、'.join(topics[:5])}等技术内容。"

    if "应包括下列内容" in t or ("应包括" in t and "内容" in t):
        if topics:
            return f"规定设计须涵盖{'、'.join(topics[:5])}等内容。"
        return "规定设计应完成的主要工作内容与技术环节。"

    if "除" in t and "外" in t and ("应采用" in t or "概率" in t) and "承载能力" not in t:
        return "要求采用概率极限状态设计法，并说明疲劳与抗震等特殊情形的规定。"

    if "承载能力极限状态" in t and "正常使用极限状态" in t:
        return "界定承载能力与正常使用两类极限状态，并分别规定应考虑的破坏模式与验算内容。"

    if "承载能力极限状态应包括" in t or ("机动体系" in t and "倾覆" in t):
        return "列举承载能力极限状态设计应计入的强度、稳定、断裂及倾覆等破坏模式。"

    if "计算结构或构件的强度" in t or ("强度" in t and "稳定性" in t and "连接" in t):
        return "规定强度、稳定性及连接强度计算时分别采用的荷载设计值与组合。"

    if "分项系数" in t and "极限状态" in t:
        return "规定采用分项系数表达式的极限状态设计方法及各状态的验算要求。"

    if "结构构件" in t and ("连接及节点" in t or "连接" in t and "节点" in t):
        return "规定结构构件、连接及节点采用的承载能力极限状态设计表达式。"

    if "应符合下列原则" in t:
        head = t.split("应符合下列原则")[0].strip("：:， ")
        head = re.sub(r"的选用$", "", head)
        if len(head) >= 4:
            return f"提出{head}的选用原则与综合比选要求。"
        return "提出结构体系或技术方案选用应遵循的原则。"

    if "应符合下列规定" in t:
        head = t.split("应符合下列规定")[0].strip("：:， ")
        if len(head) >= 4:
            return f"明确{head}应满足的具体技术要求。"
        return "明确应满足的具体技术规定。"

    if "适用于" in t:
        m = re.search(r"适用于([\u4e00-\u9fff].{2,30}?)(?:的|时|；|,)", t)
        if m:
            return f"界定适用于{m.group(1)}的情形与对象。"
        return "界定本条的适用范围与适用条件。"

    if "本方法适用于" in t or "本标准适用于" in t:
        return "说明本方法或本标准的适用工程类型与结构构件。"

    if "概率理论" in t and "极限状态" in t:
        return "要求采用概率极限状态法，并区分承载力与正常使用两类设计验算。"

    if "安全等级" in t and "使用年限" in t:
        return "规定结构安全等级划分及设计使用年限的确定依据。"

    if "荷载效应" in t or ("荷载" in t and "组合" in t):
        return "规定荷载效应组合方式及承载力、正常使用状态的设计要求。"

    if "动力荷载" in t:
        return "明确直接承受动力荷载时强度、稳定、疲劳及变形计算采用的荷载取值。"

    if "设计文件" in t and "注明" in t:
        return "规定设计文件中应载明的标准依据、构造、防护及连接等必要信息。"

    if "防连续倒塌" in t:
        return "要求重要或偶然作用结构进行防连续倒塌控制设计，保证局部失效不致整体倒塌。"

    if "抗震设防" in t or ("抗震" in t and "性能化" in t):
        return "说明抗震设计可引用的国家规范及本标准抗震性能化设计途径。"

    if "施工阶段" in t or "施工过程" in t:
        return "规定施工阶段对主体结构受力变形有显著影响时应进行施工验算。"

    if "不应低于" in t or "不得低于" in t:
        return "规定材料性能、参数或指标的下限要求。"

    if "不应" in t or "不得" in t or "严禁" in t:
        return "列出禁止性规定或必须避免的技术做法。"

    if "应按下列公式" in t or "应按下列规定" in t or "按下式" in t:
        head = re.split(r"应按|按下式", t)[0].strip("，：: ")
        if len(head) >= 6:
            return f"给出{_safe_truncate(head, 22)}的计算公式或验算规定。"
        return "给出计算公式、验算步骤或判定规定。"

    if "构造" in t and ("应" in t or "宜" in t):
        return "规定构造形式、连接细部及施工安装的技术措施。"

    if "材料" in t and ("应" in t or "宜" in t):
        return "规定材料品种、性能指标、检验及选用要求。"

    if "检验" in t or "试验" in t:
        return "规定试验检验方法、试件要求及合格判定标准。"

    if t.startswith("为使") or "制定本规范" in t or "制定本标准" in t:
        return "阐明规范制定目的与技术经济原则。"

    if "适用于" in t[:15]:
        return "说明本规范的适用工程范围与结构类型。"

    if "除应符合本规范" in t or "尚应符合国家" in t:
        return "明确与相关国家现行标准的配套执行关系。"

    if "鉴定" in t or "检测" in t:
        return "规定加固或设计前须完成的结构检测与鉴定要求。"

    if "术语" in t or num.endswith(".1") and len(t) < 30 and "定义" in t:
        return "给出该术语的定义与适用说明。"

    if topics:
        return f"围绕{'、'.join(topics[:3])}作出技术规定。"

    cleaned = re.sub(r"^(本规范|本标准|本方法|采用本方法时|当|对)", "", t)
    if "下列规定" in cleaned:
        head = cleaned.split("下列规定")[0].strip("，：: ")
        if len(head) >= 6:
            return f"明确{_safe_truncate(head, 28)}的具体要求。"

    if cleaned.startswith("规定"):
        intro = _safe_truncate(cleaned, 55)
        return intro + ("…" if len(cleaned) > 55 else "。")

    if len(cleaned) >= 10:
        intro = _safe_truncate(cleaned, 50).rstrip("，、：: ")
        return f"说明{intro}等要求。"

    return "阐明相关技术规定与执行要求。"


def summarize_entries(entries: list[tuple[str, str]], section_title: str) -> list[str]:
    """为每个三级条款生成 ### 标题 + 一句话摘要。"""
    if not entries:
        return [f"### {section_title}", "", f"收录{section_title}相关规范原文条款。", ""]

    out: list[str] = []
    for num, text in entries:
        sub_title = catalog_title(f"{num} {text}")
        out.append(f"### {sub_title}")
        out.append("")
        out.append(introduce_clause(num, text) + "。")
        out.append("")
    return out


def is_valid_section_title(title: str) -> bool:
    if len(title) > 40:
        return False
    if title.endswith(("。", "；", "，", "、")):
        return False
    if "应按本标准第" in title or "节采用" in title:
        return False
    return True


def rel_path(from_file: Path, to_file: Path) -> str:
    return Path(os.path.relpath(to_file, from_file.parent)).as_posix()


class SpecIndex:
    def __init__(self, spec_dir: Path, spec_name: str):
        self.spec_dir = spec_dir
        self.spec_name = spec_name
        self.by_key: dict[str, list[Path]] = {}
        self.chapter_dirs: dict[str, Path] = {}
        self.bodies: list[Path] = []
        self._build()

    def _build(self) -> None:
        for ch in self.spec_dir.iterdir():
            if ch.is_dir():
                m = re.match(r"^(\d+)", ch.name)
                if m:
                    self.chapter_dirs[m.group(1)] = ch
                if ch.name.startswith("附录"):
                    self.chapter_dirs[ch.name] = ch

        for md in sorted(self.spec_dir.rglob("*.md")):
            if "-index" in md.name:
                continue
            self.bodies.append(md)
            lines = md.read_text(encoding="utf-8").splitlines()
            title = section_title_from_body(lines, md.stem)
            key = expected_prefix(title)
            if key:
                self.by_key.setdefault(key, []).append(md)
            m = re.match(r"^(\d+)", title)
            if m:
                self.by_key.setdefault(m.group(1), []).append(md)

    def pick_file(self, key: str, exclude: Path | None = None) -> Path | None:
        cands = [p for p in self.by_key.get(key, []) if p != exclude]
        if not cands:
            return None
        return sorted(cands, key=lambda p: p.name)[0]

    def find_zongze(self) -> Path | None:
        ch = self.chapter_dirs.get("1")
        if not ch:
            return None
        for name in ("总则.md",):
            p = ch / name
            if p.exists():
                return p
        bodies = [p for p in ch.glob("*.md") if "-index" not in p.name]
        return bodies[0] if bodies else None

    def find_material(self) -> Path | None:
        ch = self.chapter_dirs.get("4")
        if not ch:
            return None
        return self.pick_file("4.1") or self.pick_file("4")


def related_reason(target: Path, source_key: str | None, ref: str) -> str:
    name = target.stem
    if "总则" in name or source_key == "1":
        return "规范适用范围、设计原则与基本规定"
    if ref.endswith(".1") or "设计规定" in name or "一般规定" in name:
        return f"正文引用或需结合第{ref}节设计规定"
    if "材料" in name or "4." in ref:
        return "材料性能指标、选用与检验要求"
    if "构造" in name:
        return "构造措施与节点详图要求"
    if "计算" in name or "验算" in name:
        return "承载力、稳定性或变形验算方法"
    if "连接" in name or "节点" in name:
        return "连接与节点设计计算及构造"
    if "抗震" in name:
        return "抗震性能化设计或抗震构造要求"
    return f"与第{ref}节内容直接相关"


def collect_refs(text: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for pat in (REF_INLINE_RE, REF_SECTION_RE, REF_CHAPTER_RE):
        for m in pat.finditer(text):
            key = m.group(1)
            if "." not in key and key.isdigit():
                key = key  # chapter
            if key not in seen:
                seen.add(key)
                refs.append(key)
    return refs


def build_related_links(body_path: Path, body_text: str, spec_idx: SpecIndex) -> list[str]:
    lines: list[str] = []
    used: set[Path] = {body_path}
    source_lines = body_text.splitlines()
    source_key = expected_prefix(section_title_from_body(source_lines, body_path.stem))

    def add(target: Path | None, reason: str, ref: str = "") -> None:
        if not target or target in used:
            return
        used.add(target)
        lines.append(f"- [{target.name}]({rel_path(body_path, target)})：{reason}")

    ch_dir = body_path.parent
    siblings = sorted(p for p in ch_dir.glob("*.md") if "-index" not in p.name)
    try:
        pos = siblings.index(body_path)
        if pos > 0:
            add(siblings[pos - 1], f"与本节同属{ch_dir.name}的前序内容")
        if pos < len(siblings) - 1:
            add(siblings[pos + 1], f"与本节同属{ch_dir.name}的后序内容")
    except ValueError:
        pass

    zongze = spec_idx.find_zongze()
    if zongze:
        add(zongze, "规范适用范围与基本原则")

    for ref in collect_refs(body_text):
        if "." in ref:
            target = spec_idx.pick_file(ref, exclude=body_path)
            if target:
                add(target, related_reason(target, source_key, ref), ref)
        else:
            ch_dir_ref = spec_idx.chapter_dirs.get(ref)
            if ch_dir_ref:
                first = spec_idx.pick_file(f"{ref}.1") or spec_idx.pick_file(ref)
                if first:
                    add(first, related_reason(first, source_key, ref), ref)

    if source_key:
        ch = source_key.split(".")[0]
        if ch not in ("1", "2", "3", "4") and int(ch) if ch.isdigit() else 0 > 4:
            mat = spec_idx.find_material()
            if mat and "材料" not in body_path.stem:
                add(mat, "加固或设计用材料性能指标与选用要求")

        basic = spec_idx.pick_file("3.1") or spec_idx.pick_file("3")
        if basic and source_key and not source_key.startswith("3"):
            if "加固" in spec_idx.spec_name or "设计" in spec_idx.spec_name:
                add(basic, "基本设计原则与计算假定")

    if len(lines) <= 1:
        ch_index = spec_idx.spec_dir / f"{spec_idx.spec_name}-index.md"
        if ch_index.exists():
            lines.append(
                f"- [{ch_index.name}]({rel_path(body_path, ch_index)})：查阅整本规范章节目录与分章摘要"
            )

    return lines[:6]


def strip_related_section(content: str) -> str:
    if "## 关联文件" not in content:
        return content.rstrip() + "\n"
    return content.split("## 关联文件")[0].rstrip() + "\n"


def append_related_section(body_path: Path, spec_idx: SpecIndex) -> bool:
    text = body_path.read_text(encoding="utf-8")
    if "## 关联文件" in text and "（暂无跨章引用）" not in text:
        base = strip_related_section(text)
    else:
        base = strip_related_section(text)

    links = build_related_links(body_path, base, spec_idx)
    if not links:
        links = ["- （暂无跨章引用）"]

    out = base + "\n## 关联文件\n\n" + "\n".join(links) + "\n"
    if out != text:
        body_path.write_text(out, encoding="utf-8")
        return True
    return False


def build_file_index(stem: str, body_path: Path) -> str:
    """生成文件级 index，章节目录和摘要均使用 ### 三级标题。"""
    lines = body_path.read_text(encoding="utf-8").splitlines()
    section_title = section_title_from_body(lines, stem)
    prefix = expected_prefix(section_title)
    entries = extract_entries(lines, prefix)

    catalog = [f"### [{section_title}]({stem}.md)"]
    for num, text in entries:
        sub_title = catalog_title(f"{num} {text}")
        catalog.append(f"### {sub_title}")

    out = [f"# {stem}索引", "", "## 章节目录", ""]
    out.extend(catalog)
    out.extend(["", "## 各章节内容摘要", ""])
    out.extend(summarize_entries(entries, section_title))
    return "\n".join(out).rstrip() + "\n"


def chapter_summary_line(ch_name: str, ch_dir: Path) -> list[str]:
    """为根 index 生成章级别的 ### 摘要条目。"""
    all_entries: list[tuple[str, str]] = []
    for body in sorted(ch_dir.glob("*.md")):
        if "-index" in body.name:
            continue
        lines = body.read_text(encoding="utf-8").splitlines()
        title = section_title_from_body(lines, body.stem)
        prefix = expected_prefix(title)
        all_entries.extend(extract_entries(lines, prefix))

    out: list[str] = [f"### {ch_name}", ""]
    if all_entries:
        themes = extract_themes("".join(t for _, t in all_entries))
        if themes:
            text = "、".join(themes[:4])
            if len(themes) > 4:
                text += "等"
            out.append(f"涵盖{text}等技术内容。")
        else:
            out.append("相关设计计算与构造规定。")
    else:
        out.append("相关设计计算与构造规定。")
    out.append("")
    return out


def summarize_article_body(lines: list[str]) -> str:
    for raw in lines:
        text = raw.strip()
        if not text or text.startswith("#") or text.startswith("![") or text.startswith("<!--"):
            continue
        if text.startswith("## 关联文件"):
            break
        if is_noise_line(text):
            continue
        normalized = normalize_body_text([text], 120)
        if normalized and len(normalized) >= 6:
            return normalized
    return "相关技术规定"


def build_flat_chapter_lines(ch_dir: Path) -> tuple[list[str], list[str]]:
    """Build catalog + summary lines from body .md files when no *-index.md exists."""
    catalog: list[str] = [f"### {ch_dir.name}"]
    summaries: list[str] = [f"### {ch_dir.name}", "", "条款式规范原文拆分内容。", ""]
    for body in sorted(p for p in ch_dir.glob("*.md") if "-index" not in p.name):
        slug = body.stem
        lines = body.read_text(encoding="utf-8").splitlines()
        summary = summarize_article_body(lines)
        catalog.append(f"### [{slug}]({ch_dir.name}/{slug}.md)")
        summaries.extend([f"### {slug}", "", f"{summary}。", ""])
    return catalog, summaries


def build_root_index(spec_dir: Path, spec_name: str) -> str:
    """生成根 index，章节目录：节用 ###，条款用缩进 - 列表。"""
    chapter_dirs = sorted(
        [d for d in spec_dir.iterdir() if d.is_dir()],
        key=lambda d: chapter_sort_key(d.name),
    )

    catalog: list[str] = []
    summaries: list[str] = []

    for ch_dir in chapter_dirs:
        ch_name = ch_dir.name
        section_indexes = sorted(ch_dir.glob("*-index.md"), key=lambda p: p.name)
        bodies = sorted(p for p in ch_dir.glob("*.md") if "-index" not in p.name)

        if not section_indexes and bodies:
            cat, summ = build_flat_chapter_lines(ch_dir)
            catalog.extend(cat)
            summaries.extend(summ)
            continue

        if not section_indexes:
            continue

        # 章作为 ### 条目
        catalog.append(f"### {ch_name}")
        summaries.extend(chapter_summary_line(ch_name, ch_dir))

        if not chapter_has_sections(ch_dir):
            continue

        seen_sections: set[str] = set()
        for idx in section_indexes:
            sec_stem = idx.stem.replace("-index", "")
            body = idx.with_name(f"{sec_stem}.md")
            if not body.exists():
                continue
            sec_title = section_title_from_body(body.read_text(encoding="utf-8").splitlines(), sec_stem)
            if sec_title in seen_sections or not is_valid_section_title(sec_title):
                continue
            seen_sections.add(sec_title)
            catalog.append(f"### [{sec_title}]({ch_dir.name}/{sec_stem}-index.md)")

    out = [f"# {spec_name}索引", "", "## 章节目录", ""]
    out.extend(catalog)
    out.extend(["", "## 各章节内容摘要", ""])
    out.extend(summaries)
    return "\n".join(out).rstrip() + "\n"


def find_root_index(spec_dir: Path) -> Path | None:
    """Locate the wiki root index (direct child of spec_dir/wiki)."""
    roots = sorted(spec_dir.glob("*-index.md"))
    if not roots:
        return None
    for p in roots:
        if p.name.endswith(f"{spec_dir.parent.name}-index.md"):
            return p
    for p in roots:
        if p.name == "05-final-index.md":
            return p
    return roots[0]


def sync_kb(spec_name: str) -> None:
    import shutil

    src_md = OUTPUT / spec_name / "05-final.md"
    if src_md.exists():
        shutil.copy2(src_md, KB / f"{spec_name}.md")
    src_wiki = OUTPUT / spec_name / "wiki"
    dest_wiki = KB / "wiki" / spec_name
    if dest_wiki.exists():
        shutil.rmtree(dest_wiki)
    if src_wiki.is_dir():
        shutil.copytree(src_wiki, dest_wiki)


def fill_spec(spec_name: str, *, related_links: bool = True) -> tuple[int, int]:
    spec_dir = OUTPUT / spec_name / "wiki"
    if not spec_dir.exists():
        raise FileNotFoundError(spec_dir)

    root_index = find_root_index(spec_dir)
    spec_idx = SpecIndex(spec_dir, spec_name)
    index_count = 0
    related_count = 0

    for index_path in sorted(spec_dir.rglob("*-index.md")):
        if root_index and index_path == root_index:
            continue
        stem = index_path.stem.replace("-index", "")
        body_path = index_path.with_name(f"{stem}.md")
        if not body_path.exists():
            continue
        index_path.write_text(build_file_index(stem, body_path), encoding="utf-8")
        index_count += 1

    if related_links:
        for body_path in spec_idx.bodies:
            if append_related_section(body_path, spec_idx):
                related_count += 1

    if root_index:
        root_index.write_text(build_root_index(spec_dir, spec_name), encoding="utf-8")
        index_count += 1
    return index_count, related_count


def list_all_specs() -> list[str]:
    return sorted(
        d.name
        for d in OUTPUT.iterdir()
        if d.is_dir() and (d / "wiki").is_dir()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill wiki index catalogs, summaries and related links.")
    parser.add_argument(
        "--spec",
        nargs="*",
        help="spec output directory names (default: --all or two demo specs)",
    )
    parser.add_argument("--all", action="store_true", help="process all specs under output/")
    parser.add_argument("--no-related", action="store_true", help="skip 关联文件 sections in body .md")
    parser.add_argument("--sync-kb", action="store_true", help="sync filled wiki to 知识库以及文档")
    args = parser.parse_args()

    if args.all:
        specs = list_all_specs()
    elif args.spec:
        specs = args.spec
    else:
        specs = list_all_specs()

    failed: list[str] = []
    total_idx = 0
    total_rel = 0
    for name in specs:
        try:
            idx_n, rel_n = fill_spec(name, related_links=not args.no_related)
            total_idx += idx_n
            total_rel += rel_n
            print(f"{name}: updated {idx_n} index files, {rel_n} body files with 关联文件")
            if args.sync_kb:
                sync_kb(name)
        except Exception as exc:
            failed.append(name)
            print(f"{name}: FAILED - {exc}")

    print("=" * 60)
    print(f"完成 {len(specs) - len(failed)}/{len(specs)} 份规范，共更新 {total_idx} 个 index")
    if failed:
        print("失败:", ", ".join(failed))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
