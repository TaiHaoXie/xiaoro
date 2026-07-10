"""识图Top-K质量评估脚本
遍历所有v3索引商品（rebuild_product_image_index采集的真图），
用商品原图做自搜，验证：
- Top1品牌命中率
- Top1品类命中率
- Top1同ID命中率（自搜应该回到自己）
- Top3同品牌命中率
- 低清/局部/空白拒绝率（预留）
"""
import json, os, sys, urllib.request, uuid, glob, time

sys.path.insert(0, os.getcwd())
BASE = "http://127.0.0.1:8000"


def post_image_search(path, min_score=0.3):
    boundary = "----EvalBoundary" + uuid.uuid4().hex
    files = []
    files.append(f"--{boundary}\r\n".encode())
    files.append(f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'.encode())
    files.append(b"Content-Type: image/png\r\n\r\n")
    with open(path, "rb") as f:
        files.append(f.read())
    files.append(f"\r\n--{boundary}\r\n".encode())
    files.append(f'Content-Disposition: form-data; name="min_score"\r\n\r\n{min_score}\r\n'.encode())
    files.append(f"--{boundary}--\r\n".encode())
    body = b"".join(files)
    req = urllib.request.Request(
        f"{BASE}/api/v1/image-search/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def load_product_meta():
    """从PostgreSQL加载id->(brand,category,name,image_url)映射，以及fn->id映射"""
    import psycopg2
    conn = psycopg2.connect(host="localhost", port=5432, dbname="ecommerce_agent",
                            user="xulindi", password="")
    cur = conn.cursor()
    cur.execute("SELECT id, brand, category, name, image_url FROM products")
    meta = {}
    fn_to_pid = {}
    for row in cur.fetchall():
        pid = int(row[0])
        meta[pid] = {"brand": row[1] or "", "category": row[2] or "", "name": row[3] or "", "image_url": row[4] or ""}
        iurl = row[4] or ""
        if iurl:
            fn_to_pid[os.path.basename(iurl)] = pid
    conn.close()
    return meta, fn_to_pid


def parse_v3_filename(fn, fn_to_pid):
    """优先通过DB的image_url精确映射到pid，兜底解析文件名"""
    base = os.path.basename(fn)
    if base in fn_to_pid:
        return fn_to_pid[base]
    if "_v3_" in base:
        try:
            pid_str = base.split("_v3_")[1].split("_")[0].replace(".png", "")
            return int(pid_str)
        except Exception:
            return None
    return None


def run(top_n=3, limit=None):
    print("=" * 78)
    print("识图Top-K质量评估（自搜测试）")
    print("=" * 78)

    meta, fn_to_pid = load_product_meta()
    print(f"已加载商品元数据：{len(meta)} 个，有图商品：{len(fn_to_pid)}")

    img_dir = "app/static/images/products"
    # 只评估DB里有image_url的v3图
    v3_fns = [f for f in fn_to_pid.keys() if "_v3_" in f and os.path.exists(os.path.join(img_dir, f))]
    files = [os.path.join(img_dir, f) for f in v3_fns]
    files.sort()
    if limit:
        files = files[:limit]
    print(f"待评估图片：{len(files)} 张")

    results = []
    errors = []
    for i, fp in enumerate(files, 1):
        pid = parse_v3_filename(fp, fn_to_pid)
        truth = meta.get(pid) if pid else None
        if not truth:
            continue
        try:
            t0 = time.time()
            sr = post_image_search(fp)
            dt = time.time() - t0
            res = sr.get("results", [])
            top1 = res[0] if res else {}
            topn = res[:top_n]

            top1_id = top1.get("id")
            top1_brand = top1.get("brand", "") or ""
            top1_cat = top1.get("category", "") or ""
            top1_sim = float(top1.get("similarity", 0) or 0)

            hit_id = any((r.get("id") == pid) for r in topn)
            hit_brand = any(truth["brand"] in (r.get("brand") or "") for r in topn)
            hit_cat = any(truth["category"] in (r.get("category") or "") for r in topn)
            top1_id_ok = (top1_id == pid)
            top1_brand_ok = (truth["brand"] in top1_brand)
            top1_cat_ok = (truth["category"] == top1_cat) if truth["category"] else bool(top1_cat)

            results.append({
                "file": os.path.basename(fp),
                "truth_id": pid,
                "truth_brand": truth["brand"],
                "truth_cat": truth["category"],
                "top1_id": top1_id,
                "top1_brand": top1_brand,
                "top1_cat": top1_cat,
                "top1_sim": top1_sim,
                "top1_id_ok": top1_id_ok,
                "top1_brand_ok": top1_brand_ok,
                "top1_cat_ok": top1_cat_ok,
                "topN_id_hit": hit_id,
                "topN_brand_hit": hit_brand,
                "topN_cat_hit": hit_cat,
                "n_results": len(res),
                "dt_ms": int(dt * 1000),
            })
            mark_id = "✓" if top1_id_ok else "✗"
            mark_b = "✓" if top1_brand_ok else "✗"
            mark_c = "✓" if top1_cat_ok else "✗"
            print(f"[{i:3d}/{len(files)}] {mark_id}id {mark_b}brand {mark_c}cat  "
                  f"sim={top1_sim:.1f}%  truth={truth['brand']}/{truth['category']}  "
                  f"→ top1={top1_brand}/{top1_cat}  {os.path.basename(fp)[:35]}  {int(dt*1000)}ms")
        except Exception as e:
            errors.append({"file": fp, "error": str(e)})
            print(f"[{i:3d}/{len(files)}] ERR {os.path.basename(fp)}: {e}")

    n = len(results)
    if n == 0:
        print("无有效样本")
        return

    top1_id_rate = sum(1 for r in results if r["top1_id_ok"]) / n * 100
    top1_brand_rate = sum(1 for r in results if r["top1_brand_ok"]) / n * 100
    top1_cat_rate = sum(1 for r in results if r["top1_cat_ok"]) / n * 100
    topN_brand_rate = sum(1 for r in results if r["topN_brand_hit"]) / n * 100
    topN_cat_rate = sum(1 for r in results if r["topN_cat_hit"]) / n * 100
    avg_sim = sum(r["top1_sim"] for r in results) / n
    avg_ms = sum(r["dt_ms"] for r in results) / n

    print("\n" + "=" * 78)
    print(f"评估样本数：{n}，错误数：{len(errors)}")
    print("-" * 78)
    print(f"Top1 同ID命中率：     {top1_id_rate:.1f}%  ({sum(1 for r in results if r['top1_id_ok'])}/{n})")
    print(f"Top1 品牌命中率：     {top1_brand_rate:.1f}%  ({sum(1 for r in results if r['top1_brand_ok'])}/{n})")
    print(f"Top1 品类命中率：     {top1_cat_rate:.1f}%  ({sum(1 for r in results if r['top1_cat_ok'])}/{n})")
    print(f"Top{top_n} 品牌命中率：    {topN_brand_rate:.1f}%")
    print(f"Top{top_n} 品类命中率：    {topN_cat_rate:.1f}%")
    print(f"Top1 平均相似度：     {avg_sim:.1f}%")
    print(f"平均响应时间：       {avg_ms:.0f}ms")
    print("=" * 78)

    # 输出失败样本
    failed = [r for r in results if not r["top1_id_ok"]]
    if failed:
        print(f"\nTop1未命中样本（{len(failed)}）：")
        for r in failed[:20]:
            print(f"  ✗ {r['file'][:40]}  truth={r['truth_brand']}/{r['truth_cat']}  "
                  f"→ top1={r['top1_brand']}/{r['top1_cat']} sim={r['top1_sim']:.1f}%")
    return results


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="评估前N张（0=全部）")
    ap.add_argument("--topn", type=int, default=3)
    args = ap.parse_args()
    run(top_n=args.topn, limit=args.limit or None)
