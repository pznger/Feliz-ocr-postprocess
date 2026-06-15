"""OCR 后处理总结报告。

收拢公式核对、符号定义核对、表格核对的结果，生成一份 markdown 汇总报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FixRecord:
    """单条修改记录。"""

    index: int           # 编号
    page: int | None     # PDF 页号
    line: int            # md 行号
    label: str           # 标签（如 "公式 #1"、"符号 #3"）
    original: str        # 原始内容
    corrected: str       # 修正后内容


@dataclass
class SummaryReport:
    """OCR 后处理汇总报告。"""

    spec_name: str = ""
    formula_total: int = 0
    formula_corrected: int = 0
    formula_records: list[FixRecord] = field(default_factory=list)
    symbol_total: int = 0
    symbol_corrected: int = 0
    symbol_records: list[FixRecord] = field(default_factory=list)
    table_total: int = 0
    table_corrected: int = 0
    table_records: list[FixRecord] = field(default_factory=list)
    extra_sections: list[dict[str, Any]] = field(default_factory=list)

    # ── 构建方法 ──

    def add_formula_changes(self, changes: list[Any]) -> None:
        """从 FormulaChange 列表填充公式修改记录。"""
        for i, c in enumerate(changes, 1):
            self.formula_records.append(
                FixRecord(
                    index=i,
                    page=c.page,
                    line=c.line_index + 1,
                    label=f"公式 #{i}",
                    original=c.original,
                    corrected=c.corrected,
                )
            )
        self.formula_corrected = len(self.formula_records)

    def add_symbol_changes(self, hits: list[Any]) -> None:
        """从 SymbolDefHit 列表（有 suggested_line 的）填充符号修改记录。"""
        modified = [h for h in hits if h.suggested_line]
        for i, h in enumerate(modified, 1):
            self.symbol_records.append(
                FixRecord(
                    index=i,
                    page=h.page,
                    line=h.line_index + 1,
                    label=f"符号 #{i}",
                    original=h.line_text.strip(),
                    corrected=h.suggested_line,
                )
            )
        self.symbol_corrected = len(self.symbol_records)

    def add_table_changes(self, hits: list[Any]) -> None:
        """从 TableHit 列表（有 suggested 的）填充表格修改记录。"""
        modified = [h for h in hits if h.suggested]
        for i, h in enumerate(modified, 1):
            self.table_records.append(
                FixRecord(
                    index=i,
                    page=h.page,
                    line=h.line_index + 1,
                    label=f"表格 #{i}",
                    original=h.raw_html[:200],
                    corrected=h.suggested[:200] if h.suggested else "",
                )
            )
        self.table_corrected = len(self.table_records)

    def add_extra(self, title: str, content: str) -> None:
        """添加自定义章节（如跨页断句统计、LLM 清洗统计等）。"""
        self.extra_sections.append({"title": title, "content": content})

    # ── 输出 ──

    def write(self, out_path: Path) -> None:
        """将汇总报告写入 markdown 文件。"""
        lines: list[str] = []

        lines.append(f"# OCR 后处理总结报告")
        if self.spec_name:
            lines.append(f"")
            lines.append(f"**规范名称**：{self.spec_name}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # ── 统计总览 ──
        lines.append("## 统计总览")
        lines.append("")
        lines.append("| 类别 | 扫描数 | 修正数 | 修正率 |")
        lines.append("|---|---|---|---|")

        f_rate = _pct(self.formula_corrected, self.formula_total)
        lines.append(
            f"| 公式 | {self.formula_total} | {self.formula_corrected} | {f_rate} |"
        )

        s_rate = _pct(self.symbol_corrected, self.symbol_total)
        lines.append(
            f"| 符号定义 | {self.symbol_total} | {self.symbol_corrected} | {s_rate} |"
        )

        t_rate = _pct(self.table_corrected, self.table_total)
        lines.append(
            f"| 表格 | {self.table_total} | {self.table_corrected} | {t_rate} |"
        )

        total_all = self.formula_total + self.symbol_total + self.table_total
        corrected_all = self.formula_corrected + self.symbol_corrected + self.table_corrected
        all_rate = _pct(corrected_all, total_all)
        lines.append(
            f"| **合计** | **{total_all}** | **{corrected_all}** | **{all_rate}** |"
        )
        lines.append("")

        # ── 公式修改详情 ──
        lines.append("---")
        lines.append("")
        lines.append("## 公式修改详情")
        lines.append("")
        if self.formula_records:
            lines.append(
                f"共扫描 {self.formula_total} 处公式，修正 {self.formula_corrected} 处。"
            )
            lines.append("")
            for rec in self.formula_records:
                lines.append(
                    f"### {rec.label}（PDF 页 {rec.page or '?'}，md 行 {rec.line}）"
                )
                lines.append("")
                lines.append(f"- **原始**：`{rec.original}`")
                lines.append(f"- **修正**：`{rec.corrected}`")
                lines.append("")
        else:
            lines.append(f"共扫描 {self.formula_total} 处公式，无需修正。")
            lines.append("")

        # ── 符号定义修改详情 ──
        lines.append("---")
        lines.append("")
        lines.append("## 符号定义修改详情")
        lines.append("")
        if self.symbol_records:
            lines.append(
                f"共扫描 {self.symbol_total} 处符号定义行，修正 {self.symbol_corrected} 处。"
            )
            lines.append("")
            for rec in self.symbol_records:
                lines.append(
                    f"### {rec.label}（PDF 页 {rec.page or '?'}，md 行 {rec.line}）"
                )
                lines.append("")
                lines.append(f"- **原始**：`{rec.original}`")
                lines.append(f"- **修正**：`{rec.corrected}`")
                lines.append("")
        else:
            lines.append(f"共扫描 {self.symbol_total} 处符号定义行，无需修正。")
            lines.append("")

        # ── 表格修改详情 ──
        if self.table_total > 0:
            lines.append("---")
            lines.append("")
            lines.append("## 表格修改详情")
            lines.append("")
            if self.table_records:
                lines.append(
                    f"共扫描 {self.table_total} 张表格，修正 {self.table_corrected} 张。"
                )
                lines.append("")
                for rec in self.table_records:
                    lines.append(
                        f"### {rec.label}（PDF 页 {rec.page or '?'}，md 行 {rec.line}）"
                    )
                    lines.append("")
                    lines.append(
                        f"- **原始**：\n```html\n{rec.original}\n```"
                    )
                    lines.append(
                        f"- **修正**：\n```html\n{rec.corrected}\n```"
                    )
                    lines.append("")
            else:
                lines.append(f"共扫描 {self.table_total} 张表格，无需修正。")
                lines.append("")

        # ── 额外章节 ──
        for sec in self.extra_sections:
            lines.append("---")
            lines.append("")
            lines.append(f"## {sec['title']}")
            lines.append("")
            lines.append(sec["content"])
            lines.append("")

        out_path.write_text("\n".join(lines), encoding="utf-8")


def _pct(corrected: int, total: int) -> str:
    """格式化百分比。"""
    if total == 0:
        return "—"
    ratio = corrected / total * 100
    return f"{ratio:.1f}%"
