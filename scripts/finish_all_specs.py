#!/usr/bin/env python3
"""修复剩余问题规范：从 input 重建、条款式拆分、附录 slug、全量同步。"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))
import split_wiki as sw  # noqa: E402

from postprocess.strip_frontmatter import strip_backmatter  # noqa: E402

INPUT = REPO / "input"
OUTPUT = REPO / "output"
KB = REPO / "知识库以及文档"

ARTICLE_RE = re.compile(r"^\*\*第([一二三四五六七八九十百零\d]+条)\*\*\s*(.*)")
TABLE_RE = re.compile(r"^\*\*(表[一二三四五六七八九十\d]+[：:].*)\*\*")


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", s)


def find_body_start(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if re.search(r"<!--\s*1\s*总\s*则\s*-->", line, re.I):
            return i + 1
    for i, line in enumerate(lines):
        if re.match(r"^1\.0\.1\s", line.strip()):
            return i
    for i, line in enumerate(lines):
        c = _compact(line.strip().lstrip("#"))
        if re.match(r"^1总", c) and "则" in c:
            return i
    return None


def extract_body(lines: list[str]) -> list[str]:
    start = find_body_start(lines)
    if start is None:
        raise ValueError("找不到正文起点")
    text = "\n".join(lines[start:])
    text, _ = strip_backmatter(text)
    return text.splitlines()


def normalize_headings(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        s = line.strip()
        c = _compact(s.lstrip("#"))

        if re.match(r"^1总", c) and "则" in c:
            out.extend(["# 1 总则", ""])
            continue

        if re.match(r"^1\.0\.1\s", s) and not any(x.startswith("# 1 ") for x in out[-3:]):
            out.extend(["# 1 总则", ""])

        # 假章：### 2 正常使用极限状态 / ### 1 标准组合
        if re.match(r"^#{1,3}\s*[12]\s+", s):
            t = re.sub(r"^#{1,3}\s*", "", s)
            if re.match(r"^[12]\s+(正常使用极限状态|标准组合|频遇组合)", t):
                out.append("#### " + t)
                continue

        if re.match(r"^#{1,3}\s*3\s*准永久组合", s):
            out.append("#### 3 准永久组合")
            continue

        # ### N 章（无小数点节号）
        m = re.match(r"^(#{1,3})\s*(\d{1,2})\s+([\u4e00-\u9fff][^\n]{1,30})$", s)
        if m and "." not in m.group(2):
            title = m.group(3).strip()
            if not title.endswith(("。", "；")) and "组合" not in title or int(m.group(2)) >= 3:
                if int(m.group(2)) <= 15:
                    out.append(f"# {m.group(2)} {title}")
                    continue

        # ## N 章
        m2 = re.match(r"^##\s*(\d{1,2})\s+([\u4e00-\u9fff].+)$", s)
        if m2 and not re.match(r"^\d+\.\d", m2.group(0).lstrip("#")):
            out.append(f"# {m2.group(1)} {m2.group(2)}")
            continue

        # ### / #### N.M 节 -> ## N.M（附录内 D.1 等）
        m3 = re.match(r"^#{1,6}\s*(\d+\.\d+(?:\.\d+)?)\s*(.*)$", s)
        if m3 and not s.lstrip("#").strip().startswith("附录"):
            rest = m3.group(2).strip() or m3.group(1)
            out.append(f"## {m3.group(1)} {rest}".strip())
            continue

        # ### 2术语 -> # 2 术语
        m4 = re.match(r"^#{1,3}\s*(\d{1,2})([\u4e00-\u9fff].+)$", _compact(s.lstrip("#")))
        if m4 and len(m4.group(2)) <= 20:
            out.append(f"# {m4.group(1)} {m4.group(2)}")
            continue

        # 附录
        m5 = re.match(r"^#{1,6}\s*附录\s*([A-Z])\s*(.*)$", s)
        if m5 and len(s) < 80:
            out.append(f"# 附录 {m5.group(1)} {m5.group(2).strip()}".strip())
            continue

        out.append(line)

    # 补 # 3 基本规定（GB50153：有 ## 3.1 无 # 3）
    for i, line in enumerate(out):
        m = re.match(r"^##\s*3\.1\s", line.strip())
        if m and not any(x.strip().startswith("# 3 ") for x in out[:i]):
            out.insert(i, "# 3 基本规定")
            out.insert(i + 1, "")
            break

    return out


def rebuild_from_input(spec_name: str, input_name: str | None = None) -> None:
    inp = INPUT / (input_name or f"{spec_name}.md")
    if not inp.exists():
        raise FileNotFoundError(inp)
    lines = inp.read_text(encoding="utf-8").splitlines()
    body = extract_body(lines)
    fixed = normalize_headings(body)
    out_md = OUTPUT / spec_name / "05-final.md"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(fixed) + "\n", encoding="utf-8")


def resplit(md: Path) -> None:
    wiki = md.parent / "wiki"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "split_wiki.py"),
            str(md),
            "--output-root",
            str(wiki),
            "--spec-name",
            ".",
        ],
        check=True,
        cwd=str(REPO),
    )


def split_articles(md: Path, spec_name: str) -> None:
    lines = md.read_text(encoding="utf-8").splitlines()
    wiki = md.parent / "wiki"
    if wiki.exists():
        shutil.rmtree(wiki)
    wiki.mkdir(parents=True)
    body_dir = wiki / "正文"
    body_dir.mkdir()

    chunks: list[tuple[str, list[str]]] = []
    cur_title = "概述"
    cur_lines: list[str] = [f"# {spec_name}", ""]

    for line in lines:
        am = ARTICLE_RE.match(line.strip())
        tm = TABLE_RE.match(line.strip())
        if am:
            if cur_lines:
                chunks.append((cur_title, cur_lines))
            num = am.group(1)
            rest = am.group(2).strip()
            cur_title = num if num.startswith("第") else f"第{num}"
            cur_lines = [f"## {cur_title}", ""]
            if rest:
                cur_lines.append(rest)
                cur_lines.append("")
        elif tm:
            if cur_lines:
                chunks.append((cur_title, cur_lines))
            cur_title = tm.group(1).replace(":", "：")
            cur_lines = [f"## {cur_title}", ""]
        else:
            cur_lines.append(line)

    if cur_lines:
        chunks.append((cur_title, cur_lines))

    index_lines = [f"# {spec_name}索引", "", "## 章节目录", ""]
    for title, chunk_lines in chunks:
        slug = sw.slug_title(title)[:60] or "chunk"
        if slug == "概述" and len(chunk_lines) < 5:
            continue
        write_path = body_dir / f"{slug}.md"
        write_path.write_text("\n".join(chunk_lines).strip() + "\n", encoding="utf-8")
        index_lines.append(f"### {slug}")
        index_lines.extend(["", "TODO: 待LLM总结", ""])

    (wiki / f"{spec_name}-index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")


def split_single_doc(md: Path, spec_name: str) -> None:
    wiki = md.parent / "wiki"
    if wiki.exists():
        shutil.rmtree(wiki)
    wiki.mkdir(parents=True)
    content = md.read_text(encoding="utf-8")
    slug = "文档全文"
    d = wiki / slug
    d.mkdir()
    (d / f"{slug}.md").write_text(content, encoding="utf-8")
    idx = f"# {spec_name}索引\n\n## 章节目录\n\n### {slug}\n\n## 各章节内容摘要\n\n### {slug}\n\nTODO: 待LLM总结\n"
    (d / f"{slug}-index.md").write_text(idx, encoding="utf-8")
    (wiki / f"{spec_name}-index.md").write_text(idx, encoding="utf-8")


def sync_kb(name: str) -> None:
    shutil.copy2(OUTPUT / name / "05-final.md", KB / f"{name}.md")
    dest = KB / "wiki" / name
    src = OUTPUT / name / "wiki"
    if dest.exists():
        shutil.rmtree(dest)
    if src.is_dir():
        shutil.copytree(src, dest)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sync-kb", action="store_true", default=True)
    args = p.parse_args()

    actions: list[str] = []

    # 1. 从 input 重建
    for spec in [
        "GB50067-2014汽车库、修车库、停车场设计防火规范",
        "GB 50153-2008 工程结构可靠性设计统一标准",
    ]:
        rebuild_from_input(spec)
        resplit(OUTPUT / spec / "05-final.md")
        actions.append(f"rebuild+split: {spec}")

    # 2. 条款式
    parking = "10-《停车场规划设计规则(试行)》（公安部建设部〔88〕公(交管)字90号)"
    split_articles(OUTPUT / parking / "05-final.md", parking)
    actions.append(f"articles: {parking}")

    # 3. OCR 不完整 -> 单文件 wiki
    school = "GB 50099-2011《中小学校设计规范》GB 50099"
    split_single_doc(OUTPUT / school / "05-final.md", school)
    actions.append(f"single-doc: {school} (OCR仅封面)")

    # 4. 附录 slug 修复后重拆
    for spec in ["建筑桩基技术规范", "建筑给水排水设计标准", "砌体结构设计规范"]:
        resplit(OUTPUT / spec / "05-final.md")
        actions.append(f"resplit-appendix: {spec}")

    # 5. 全量同步
    if args.sync_kb:
        for d in sorted(OUTPUT.iterdir()):
            if d.is_dir() and (d / "05-final.md").exists():
                sync_kb(d.name)

    print("=" * 60)
    for a in actions:
        print(" ", a)

    # 最终统计
    ok = no_parse = wiki_gap = 0
    for d in sorted(OUTPUT.iterdir()):
        if not d.is_dir():
            continue
        md = d / "05-final.md"
        if not md.exists():
            continue
        lines = md.read_text(encoding="utf-8").splitlines()
        try:
            ch = sw.parse_structure(lines)
            exp = {sw.slug_title(c.title) for c in ch if sw.chapter_number(c.title) or sw.is_appendix_title(c.title)}
            act = {p.name for p in (d / "wiki").iterdir() if p.is_dir()} if (d / "wiki").is_dir() else set()
            if exp - act:
                wiki_gap += 1
            else:
                ok += 1
        except ValueError:
            if (d / "wiki").is_dir():
                ok += 1
            else:
                no_parse += 1

    print(f"\n标准章拆分 OK: {ok}  特殊wiki: {wiki_gap + no_parse}")


if __name__ == "__main__":
    main()
