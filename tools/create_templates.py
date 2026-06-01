from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config_path = ROOT / "配置表" / "报表工具配置模板.xlsx"
    template_path = ROOT / "配置表" / "输出报表模板.xlsx"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    build_config(config_path)
    build_output_template(template_path)
    print(f"配置模板已生成：{config_path}")
    print(f"输出模板已生成：{template_path}")


def build_config(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "区域负责人"
    write_table(
        ws,
        ["所在省份", "所在城市", "运营经理", "大区"],
        [
            ["广东省", "广州市", "杨昊", "华东"],
            ["湖北省", "武汉市", "胡金伟", "华中"],
            ["上海市", "上海市", "肖炳宇", "华北"],
        ],
    )

    ws = wb.create_sheet("字段映射")
    write_table(
        ws,
        ["平台", "原始字段", "标准字段", "字段类型", "是否输出", "是否汇总", "汇总方式", "是否日期字段"],
        [
            ["美团", "日期", "统计日期", "日期", "是", "否", "", "是"],
            ["美团", "门店名称", "门店名", "文本", "是", "否", "", "否"],
            ["美团", "门店ID", "门店ID", "文本", "是", "否", "", "否"],
            ["美团", "订单数", "订单数", "数值", "是", "是", "求和", "否"],
            ["美团", "销售额", "销售额", "数值", "是", "是", "求和", "否"],
            ["美团", "好评数", "好评数", "数值", "是", "是", "求和", "否"],
            ["美团", "美团星级(星)", "美团星级", "数值", "是", "是", "平均", "否"],
            ["美团", "经营评分牌级", "牌级别", "文本", "是", "是", "计数", "否"],
            ["美团", "门店名称", "门店计数", "文本", "否", "是", "计数", "否"],
            ["抖音", "统计日期", "统计日期", "日期", "是", "否", "", "是"],
            ["抖音", "门店", "门店名", "文本", "是", "否", "", "否"],
            ["抖音", "门店ID", "门店ID", "文本", "是", "否", "", "否"],
            ["抖音", "支付订单数", "订单数", "数值", "是", "是", "求和", "否"],
            ["抖音", "支付金额", "销售额", "数值", "是", "是", "求和", "否"],
            ["抖音", "好评", "好评数", "数值", "是", "是", "求和", "否"],
        ],
    )

    ws = wb.create_sheet("门店对照")
    write_table(
        ws,
        ["平台", "门店名", "门店ID", "省份", "城市"],
        [
            ["", "示例门店A", "1001", "广东省", "广州市"],
            ["", "示例门店B", "1002", "湖北省", "武汉市"],
        ],
    )

    ws = wb.create_sheet("排行榜配置")
    write_table(
        ws,
        ["榜单名称", "数据范围", "统计维度", "指标字段", "排序", "TopN", "输出尺寸", "是否启用", "单位", "筛选字段", "筛选值"],
        [
            ["大区销售额排行榜", "合并", "大区", "销售额", "desc", 10, "全部", "是", "元", "", ""],
            ["门店好评排行榜", "合并", "门店名", "好评数", "desc", 10, "全部", "是", "", "", ""],
            ["金牌大区门店数", "合并", "大区", "牌级别", "desc", 10, "全部", "否", "家", "牌级别", "金牌"],
        ],
    )

    ws = wb.create_sheet("综合简报")
    write_table(
        ws,
        [
            "简报名称",
            "数据范围",
            "输出尺寸",
            "是否启用",
            "门店字段",
            "分组字段",
            "评分字段",
            "星级字段",
            "对比星级字段",
            "等级字段",
            "评价数字段",
            "好评数字段",
            "差评数字段",
            "订单字段",
            "TopN",
        ],
        [
            [
                "门店评级与评价综合简报",
                "美团",
                "汇报横版",
                "是",
                "门店名",
                "大区",
                "经营评分",
                "美团星级",
                "点评星级",
                "牌级别",
                "新增评价",
                "新增好评",
                "新增差评",
                "核销单量",
                12,
            ]
        ],
    )
    wb.save(path)


def build_output_template(path: Path) -> None:
    wb = Workbook()
    wb.active.title = "合并明细"
    for sheet in ["美团明细", "抖音明细", "美团流量汇总", "美团经营汇总", "抖音汇总", "合并汇总", "处理说明"]:
        wb.create_sheet(sheet)
    wb.save(path)


def write_table(ws, headers: list[str], rows: list[list[object]]) -> None:
    ws.append(headers)
    for row in rows:
        ws.append(row)
    fill = PatternFill("solid", fgColor="1F4E78")
    font = Font(bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    for idx, header in enumerate(headers, 1):
        width = max(len(str(header)) + 4, 14)
        ws.column_dimensions[get_column_letter(idx)].width = width


if __name__ == "__main__":
    main()
