"""
从飞书 seed 表拉取美妆护肤商品，生成本地可复现的 JSON。

逐列读取（每列单独取值，避免整行 CSV 里字段内逗号/换行破坏解析），
按 [row=N] 前缀重组多行单元格，输出 data/beauty_products_seed.json。

用法：
    .venv/bin/python scripts/build_beauty_seed.py
"""

import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LARK_CLI = "/opt/homebrew/Cellar/node/26.0.0/lib/node_modules/@larksuite/cli/bin/lark-cli"
SPREADSHEET_TOKEN = "JkZMsTjp2hVutut56zYlT5gag91"
SHEET_NAME = "美妆护肤商品Seed"
LAST_ROW = 57

# 列字母 -> 字段名
COLUMNS = {
    "A": "name",
    "B": "brand",
    "C": "category",
    "D": "subcategory",
    "E": "price",
    "F": "price_band",
    "G": "price_updated_at",
    "H": "suitable_skin_types",
    "I": "target_users",
    "J": "key_ingredients",
    "K": "concerns",
    "L": "positioning",
    "M": "pitfalls",
    "N": "detail_url",
    "O": "source_type",
}

ROW_PREFIX = re.compile(r"^\[row=(\d+)\]\s?(.*)$")


def read_column(col: str) -> dict:
    """读取单列，返回 {row_number: cell_value}。"""
    rng = f"{col}1:{col}{LAST_ROW}"
    out = subprocess.run(
        [
            LARK_CLI, "sheets", "+csv-get",
            "--spreadsheet-token", SPREADSHEET_TOKEN,
            "--sheet-name", SHEET_NAME,
            "--range", rng,
            "--as", "user",
            "--format", "json",
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        print(f"读取列 {col} 失败：{out.stderr[:300]}", file=sys.stderr)
        sys.exit(1)

    payload = json.loads(out.stdout)
    annotated = payload["data"]["annotated_csv"]

    cells: dict[int, str] = {}
    current_row = None
    for line in annotated.split("\n"):
        m = ROW_PREFIX.match(line)
        if m:
            current_row = int(m.group(1))
            cells[current_row] = m.group(2)
        elif current_row is not None:
            cells[current_row] += "\n" + line
    return cells


def main() -> None:
    print("逐列拉取飞书 seed 表...")
    columns_data = {field: read_column(col) for col, field in COLUMNS.items()}

    products = []
    for row in range(2, LAST_ROW + 1):  # row=1 是表头
        record = {field: (columns_data[field].get(row, "") or "").strip()
                  for field in COLUMNS.values()}
        if not record["name"]:
            continue
        products.append(record)

    out_path = PROJECT_ROOT / "data" / "beauty_products_seed.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "feishu:美妆护肤商品Seed",
        "source_batch": "feishu_seed_202606",
        "spreadsheet_token": SPREADSHEET_TOKEN,
        "count": len(products),
        "products": products,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 已生成 {out_path}，共 {len(products)} 条商品")


if __name__ == "__main__":
    main()
