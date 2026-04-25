"""
薬価データ取得・JSON変換スクリプト

社会保険診療報酬支払基金の医薬品マスターCSVを取得し、
data/yakka.json として保存する。

実行方法:
    python scripts/fetch_yakka.py

必要ライブラリ:
    pip install requests beautifulsoup4
"""

import csv
import io
import json
import os
import re
import sys
import zipfile
from datetime import date

import requests
from bs4 import BeautifulSoup

# ===== 設定 =====
MASTER_PAGE_URL = "https://www.ssk.or.jp/seikyushiharai/tensuhyo/kihonmasta/kihonmasta_04.html"
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "yakka.json")
MIN_COUNT_RATIO = 0.5


def fetch_zip_url(session: requests.Session) -> str:
    res = session.get(MASTER_PAGE_URL, timeout=30)
    res.raise_for_status()
    soup = BeautifulSoup(res.content, "html.parser")

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".zip"):
            if href.startswith("http"):
                return href
            from urllib.parse import urljoin
            return urljoin(MASTER_PAGE_URL, href)

    raise RuntimeError(
        "ZIPファイルのURLが見つかりませんでした。"
        f"URL: {MASTER_PAGE_URL}"
    )


def download_and_extract_csv(session: requests.Session, zip_url: str) -> str:
    print(f"ZIPをダウンロード中: {zip_url}")
    res = session.get(zip_url, timeout=120)
    res.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError("ZIP内にCSVファイルが見つかりませんでした。")
        csv_name = csv_names[0]
        print(f"CSV展開中: {csv_name}")
        raw_bytes = zf.read(csv_name)

    return raw_bytes.decode("cp932", errors="replace")


def detect_columns(sample_rows: list[list[str]]) -> tuple[int, int, int, int]:
    """
    CSVの列構成を検出する。

    支払基金の医薬品マスターCSVは以下の構造になっている：
    [種別] [薬剤コード(9桁)] [変更区分] [薬剤名] [名称文字数] [規格容量] [規格文字数] [単位] [単位文字数] [算定区分] [薬価] ...

    各フィールドの後ろに「文字数」列が続くため、
    薬剤名列(name_col)を基準に固定オフセットで残りを計算する。
    """
    rows = [r for r in sample_rows if len(r) >= 8]
    if not rows:
        raise RuntimeError("列検出に使えるデータ行がありません。")

    n_cols = max(len(r) for r in rows)
    n = len(rows)

    code_col = name_col = -1

    for col in range(n_cols):
        vals = [r[col].strip() for r in rows if col < len(r)]
        if not vals:
            continue

        # 9桁の数字が多い列 → 薬剤コード
        nine_digit = sum(1 for v in vals if re.match(r"^\d{9}$", v))
        if nine_digit / n > 0.7 and code_col == -1:
            code_col = col
            print(f"  列{col}: 薬剤コード（例: {vals[0]}）")
            continue

        # 日本語文字を含み比較的長い列 → 薬剤名
        jp_long = sum(1 for v in vals if re.search(r"[ぁ-んァ-ヶ一-龥]", v) and len(v) >= 3)
        if jp_long / n > 0.5 and name_col == -1:
            name_col = col
            print(f"  列{col}: 薬剤名（例: {vals[0]}）")
            break  # 薬剤名が見つかれば残りは固定オフセットで計算

    if name_col == -1:
        raise RuntimeError("薬剤名列を検出できませんでした。")

    # 薬剤名の後ろは固定構造:
    # +1: 名称文字数, +2: 規格容量, +3: 規格文字数, +4: 単位, +5: 単位文字数, +6: 算定区分, +7: 薬価
    unit_col  = name_col + 4
    price_col = name_col + 7

    print(f"  列{unit_col}: 単位（名称列+4）")
    print(f"  列{price_col}: 薬価（名称列+7）")

    return code_col, name_col, unit_col, price_col


def parse_drugs(csv_text: str) -> list[dict]:
    reader = csv.reader(io.StringIO(csv_text))
    all_rows = list(reader)

    print(f"総行数: {len(all_rows)}")

    # 最初の20行を表示して構造確認
    print("--- CSVサンプル（最初の5行）---")
    for i, row in enumerate(all_rows[:5]):
        print(f"  行{i} ({len(row)}列): {row[:12]}")  # 最大12列まで表示
    print("---")

    # 列を自動検出（最初の200行をサンプルに使用）
    print("列を自動検出中...")
    code_col, name_col, unit_col, price_col = detect_columns(all_rows[:200])

    if -1 in (code_col, name_col, unit_col, price_col):
        print(f"警告: 自動検出結果 → code={code_col}, name={name_col}, unit={unit_col}, price={price_col}")
        print("フォールバック: 設計書の列定義を使用します（code=0, name=2, unit=3, price=8）")
        code_col  = code_col  if code_col  != -1 else 0
        name_col  = name_col  if name_col  != -1 else 2
        unit_col  = unit_col  if unit_col  != -1 else 3
        price_col = price_col if price_col != -1 else 8

    print(f"使用列: code={code_col}, name={name_col}, unit={unit_col}, price={price_col}")

    drugs = []
    max_col = max(code_col, name_col, unit_col, price_col)

    for row in all_rows:
        if len(row) <= max_col:
            continue

        code      = row[code_col].strip()
        name      = row[name_col].strip()
        unit      = row[unit_col].strip()
        price_raw = row[price_col].strip()

        if not price_raw.lstrip("-").isdigit():
            continue
        if not name or not code:
            continue
        # 日本語文字がない行は薬剤名でない可能性が高いのでスキップ
        if not re.search(r"[ぁ-んァ-ヶ一-龥]", name):
            continue

        price = round(int(price_raw) / 100, 2)
        drugs.append({"code": code, "name": name, "unit": unit, "price": price})

    return drugs


def load_existing_count(output_path: str) -> int:
    try:
        with open(output_path, encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("count", 0))
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return 0


def save_json(drugs: list[dict], output_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    payload = {
        "updated_at": date.today().isoformat(),
        "source":     "社会保険診療報酬支払基金 医薬品マスター",
        "count":      len(drugs),
        "drugs":      drugs,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"保存完了: {output_path}（{len(drugs):,}件）")


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; yakka-fetcher/1.0)"})

    print("支払基金ページをスクレイピング中...")
    zip_url = fetch_zip_url(session)

    csv_text = download_and_extract_csv(session, zip_url)

    print("CSVをパース中...")
    drugs = parse_drugs(csv_text)

    if len(drugs) == 0:
        raise RuntimeError("薬剤データが1件も取得できませんでした。")

    prev_count = load_existing_count(OUTPUT_PATH)
    if prev_count > 0:
        ratio = len(drugs) / prev_count
        if ratio < MIN_COUNT_RATIO:
            raise RuntimeError(
                f"取得件数が前回比 {ratio:.0%} で異常に少ないです。"
                f"（今回: {len(drugs):,}件、前回: {prev_count:,}件）"
            )

    print(f"取得件数: {len(drugs):,}件（前回: {prev_count:,}件）")
    save_json(drugs, OUTPUT_PATH)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
