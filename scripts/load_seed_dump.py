"""
一键导入满血版 seed 数据（商品 + 知识库 + 知识文档登记）

把 data/seed_dump.sql（pg_dump 导出的 products / knowledge_base /
knowledge_documents 三张表，含表结构与全部数据）导入 PostgreSQL。
配合仓库内 app/static/images/products/ 下已随代码提供的 103 张商品图，
别人拉下代码后跑这一个脚本就能得到与作者本地一致的满血版数据。

前提：
  1. PostgreSQL 已启动，且 .env / app.config 里的连接信息正确
  2. 数据库已存在（应用首次启动会自动建表；本脚本用的表结构 dump 里也带）

用法：
    .venv/bin/python scripts/load_seed_dump.py
    .venv/bin/python scripts/load_seed_dump.py --reset   # 先 DROP 掉三张表再全量重建
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402

SEED_SQL = PROJECT_ROOT / "data" / "seed_dump.sql"
SEED_TABLES = ("products", "knowledge_base", "knowledge_documents")


def _psql_base_cmd() -> list:
    return [
        "psql",
        "-h", settings.POSTGRES_HOST,
        "-p", str(settings.POSTGRES_PORT),
        "-U", settings.POSTGRES_USER,
        "-d", settings.POSTGRES_DB,
        "-v", "ON_ERROR_STOP=1",
    ]


def _run(cmd: list, env_extra: dict) -> None:
    import os
    env = os.environ.copy()
    env.update(env_extra)
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        raise SystemExit(f"命令失败（退出码 {result.returncode}）：{' '.join(cmd)}")


def main(reset: bool) -> None:
    if not SEED_SQL.exists():
        raise SystemExit(f"找不到 seed 文件：{SEED_SQL}")

    env_extra = {"PGPASSWORD": settings.POSTGRES_PASSWORD or ""}

    if reset:
        drop = "DROP TABLE IF EXISTS " + ", ".join(SEED_TABLES) + " CASCADE;"
        print(f"[reset] 删除旧表：{', '.join(SEED_TABLES)}")
        _run(_psql_base_cmd() + ["-c", drop], env_extra)

    print(f"[load] 导入 {SEED_SQL.relative_to(PROJECT_ROOT)} ...")
    _run(_psql_base_cmd() + ["-f", str(SEED_SQL)], env_extra)

    # 计数校验
    counts = "; ".join(f"SELECT '{t}', COUNT(*) FROM {t}" for t in SEED_TABLES)
    print("[verify] 各表行数：")
    _run(_psql_base_cmd() + ["-c", counts.replace("; ", " UNION ALL ")], env_extra)
    print("✅ seed 数据导入完成")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导入满血版 seed 数据")
    parser.add_argument("--reset", action="store_true", help="先 DROP 三张表再全量重建")
    args = parser.parse_args()
    main(args.reset)
