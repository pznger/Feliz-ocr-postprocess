#!/usr/bin/env python3
"""检查 output 中各规范 wiki 是否缺章/缺节。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import split_wiki as sw  # noqa: E402

H1 = re.compile(r"^#(?!#)\s*(.+?)\s*$")
H2 = re.compile(r"^##(?!#)\s*(.+?)\s*$")
PLAIN_CH = re.compile(r"^(\d+)\s+([\u4e00-\u9fff].+)$")


def audit_spec(spec_dir: Path) -> tuple[list[str], str | None]:
    name = spec_dir.name
    md = spec_dir / "05-final.md"
    wiki = spec_dir / "wiki"
    if not md.exists():
        return [], "无 05-final.md"

    lines = md.read_text(encoding="utf-8").splitlines()
    issues: list[str] = []

    try:
        chapters = sw.parse_structure(lines)
    except Exception as exc:
        return [], str(exc)

    parsed_ch_nums = [sw.chapter_number(c.title) for c in chapters if sw.chapter_number(c.title)]
    parsed_appendix = [c.title for c in chapters if sw.is_appendix_title(c.title)]

    doc_h1_chapters: list[tuple[int, int, str]] = []
    doc_h1_appendix: list[tuple[int, str]] = []
    in_tail = False
    for i, line in enumerate(lines):
        m = H1.match(line)
        if not m:
            continue
        title = sw.normalize_title(m.group(1))
        if sw.TAIL_HEADING_RE.match(title.replace(" ", "")):
            in_tail = True
        if in_tail:
            continue
        if sw.is_appendix_title(title):
            doc_h1_appendix.append((i + 1, title))
        elif sw.is_chapter_title(title):
            n = sw.chapter_number(title)
            if n:
                doc_h1_chapters.append((i + 1, n, title))

    if parsed_ch_nums:
        mx = max(parsed_ch_nums)
        missing_nums = [n for n in range(1, mx + 1) if n not in parsed_ch_nums]
        if missing_nums:
            issues.append(f"解析缺章: {missing_nums}")

    for ln, n, title in doc_h1_chapters:
        if n not in parsed_ch_nums:
            issues.append(f"文档有 H1 章 #{n} 未解析 (L{ln}): {title[:50]}")

    doc_app_letters: set[str] = set()
    for _ln, t in doc_h1_appendix:
        m = sw.APPENDIX_RE.match(t)
        if m:
            doc_app_letters.add(m.group(1))
    parsed_app_letters: set[str] = set()
    for t in parsed_appendix:
        m = sw.APPENDIX_RE.match(t)
        if m:
            parsed_app_letters.add(m.group(1))
    miss_app = sorted(doc_app_letters - parsed_app_letters)
    if miss_app:
        issues.append(f"缺附录: {miss_app}")

    try:
        start = sw.first_content_index(lines)
    except ValueError:
        start = 0
    plain_missing_hash: list[tuple[int, str]] = []
    max_ch = max(parsed_ch_nums) if parsed_ch_nums else 20
    for i in range(start, len(lines)):
        line = lines[i].strip()
        if not line or line.startswith("#"):
            continue
        pm = PLAIN_CH.match(line)
        if pm and int(pm.group(1)) <= max_ch + 2:
            n = int(pm.group(1))
            if n not in parsed_ch_nums and n <= max_ch:
                plain_missing_hash.append((i + 1, line[:60]))
    if plain_missing_hash:
        ln, sample = plain_missing_hash[0]
        issues.append(
            f"疑似缺 # 章标题 {len(plain_missing_hash)} 处, 例 L{ln}: {sample}"
        )

    if wiki.is_dir():
        src_lines = len(lines)
        split_lines = sum(
            len(p.read_text(encoding="utf-8").splitlines())
            for p in wiki.rglob("*.md")
            if not p.name.endswith("-index.md")
        )
        ratio = split_lines / src_lines if src_lines else 0
        if ratio < 0.75:
            issues.append(f"wiki 行数偏少 ratio={ratio:.2%} ({split_lines}/{src_lines})")

    if parsed_ch_nums:
        doc_sections: set[str] = set()
        for line in lines:
            m = H2.match(line)
            if m:
                t = sw.normalize_title(m.group(1))
                sm = sw.SECTION_NO_RE.match(t)
                if sm:
                    doc_sections.add(sm.group(1))
        parsed_sections: set[str] = set()
        for ch in chapters:
            for sec in ch.sections:
                sm = sw.SECTION_NO_RE.match(sw.normalize_title(sec.title))
                if sm:
                    parsed_sections.add(sm.group(1))
        miss_sec = sorted(doc_sections - parsed_sections, key=lambda x: [int(p) for p in x.split(".")])
        if miss_sec:
            issues.append(f"缺节 {len(miss_sec)} 个, 例: {miss_sec[:8]}")

    return issues, None


def main() -> None:
    p = argparse.ArgumentParser(description="Audit wiki chapter gaps")
    p.add_argument("--output-root", type=Path, default=REPO / "output")
    p.add_argument("--only-issues", action="store_true")
    args = p.parse_args()

    ok: list[str] = []
    issues_map: dict[str, list[str]] = {}
    non_standard: dict[str, str] = {}

    for spec_dir in sorted(args.output_root.iterdir()):
        if not spec_dir.is_dir():
            continue
        iss, err = audit_spec(spec_dir)
        if err:
            non_standard[spec_dir.name] = err
        elif iss:
            issues_map[spec_dir.name] = iss
        else:
            ok.append(spec_dir.name)

    print("=" * 60)
    print(f"OK: {len(ok)}  有问题: {len(issues_map)}  非标准结构: {len(non_standard)}")
    print("=" * 60)

    for name, iss in issues_map.items():
        print(f"\n[{name}]")
        for x in iss:
            print(f"  - {x}")

    if non_standard:
        print("\n--- 非标准 (条款式 / 无 # 1) ---")
        for name, e in non_standard.items():
            print(f"  {name}: {e[:80]}")

    if not args.only_issues and ok:
        print(f"\n--- 无问题 ({len(ok)}) ---")


if __name__ == "__main__":
    main()
