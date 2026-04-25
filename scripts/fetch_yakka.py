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
import sys
import zipfile
from datetime import date

import requests
from bs4 import BeautifulSoup

# ===== 設定 =====
MASTER_PAGE_URL = "https://www.ssk.or.jp/seikyushiharai/tensuhyo/kihonmasta/kihonmasta_04.html"
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "yakka.json")
MIN_COUNT_RATIO = 0.5  # 前回比この割合を下回ったら異常とみなす

# CSV列インデックス
COL_CODE  = 0
COL_NAME  = 2
COL_UNIT  = 3
COL_PRICE = 8


def fetch_zip_url(session: requests.Session) -> str:
    """マスターページをスクレイピングしてZIPのダウンロードURLを取得する。"""
    res = session.get(MASTER_PAGE_URL, timeout=30)
    res.raise_for_status()
    soup = BeautifulSoup(res.content, "html.parser")

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".zip"):
            if href.startswith("http"):
                return href
            # 相対URLを絶対URLに変換
            from urllib.parse import urljoin
            return urljoin(MASTER_PAGE_URL, href)

    raise RuntimeError(
        "ZIPファイルのURLが見つかりませんでした。"
        "支払基金のページ構造が変わった可能性があります。"
        f"URL: {MASTER_PAGE_URL}"
    )


def download_and_extract_csv(session: requests.Session, zip_url: str) -> str:
    """ZIPをダウンロードして内部のCSV文字列（Shift-JIS→UTF-8）を返す。"""
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

    # Shift-JIS (cp932) でデコード
    return raw_bytes.decode("cp932", errors="replace")


def parse_drugs(csv_text: str) -> list[dict]:
    """CSVをパースして薬剤リストを返す。"""
    drugs = []
    reader = csv.reader(io.StringIO(csv_text))

    for row_num, row in enumerate(reader, start=1):
        # 列数が不足している行はスキップ
        if len(row) <= COL_PRICE:
            continue

        code      = row[COL_CODE].strip()
        name      = row[COL_NAME].strip()
        unit      = row[COL_UNIT].strip()
        price_raw = row[COL_PRICE].strip()

        # 薬価が数値でない行（ヘッダー行など）はスキップ
        if not price_raw.lstrip("-").isdigit():
            continue

        # 必須フィールドが空の行はスキップ
        if not name or not code:
            continue

        # CSVの薬価は 0.1銭（0.001円）刻みの整数 → 100で割って円換算
        price = round(int(price_raw) / 100, 2)

        drugs.append({
            "code":  code,
            "name":  name,
            "unit":  unit,
            "price": price,
        })

    return drugs


def load_existing_count(output_path: str) -> int:
    """既存の yakka.json の count を返す。ファイルがなければ 0。"""
    try:
        with open(output_path, encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("count", 0))
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return 0


def save_json(drugs: list[dict], output_path: str) -> None:
    """薬剤リストを yakka.json として保存する。"""
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

    # ① ZIPのURL取得
    print("支払基金ページをスクレイピング中...")
    zip_url = fetch_zip_url(session)

    # ② ZIP ダウンロード・展開
    csv_text = download_and_extract_csv(session, zip_url)

    # ③ CSVパース
    print("CSVをパース中...")
    drugs = parse_drugs(csv_text)

    # ④ 取得件数チェック（0件で即失敗）
    if len(drugs) == 0:
        raise RuntimeError("薬剤データが1件も取得できませんでした。CSVフォーマットを確認してください。")

    # ⑤ 前回比チェック（50%未満で失敗・上書きしない）
    prev_count = load_existing_count(OUTPUT_PATH)
    if prev_count > 0:
        ratio = len(drugs) / prev_count
        if ratio < MIN_COUNT_RATIO:
            raise RuntimeError(
                f"取得件数が前回比 {ratio:.0%} で異常に少ないです。"
                f"（今回: {len(drugs):,}件、前回: {prev_count:,}件）"
                "データを上書きせずに終了します。"
            )

    print(f"取得件数: {len(drugs):,}件（前回: {prev_count:,}件）")

    # ⑥ JSON保存
    save_json(drugs, OUTPUT_PATH)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
