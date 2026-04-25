# 小分け依頼書アプリ 構造定義書

## 1. リポジトリ構成

```
kusuri-kowake/（GitHubリポジトリ）
│
├── index.html                        ← アプリ本体（HTML/CSS/JS 1ファイル統合）
│
├── data/
│   └── yakka.json                    ← 変換済み薬価データ（GitHub Actionsが自動更新）
│
├── scripts/
│   └── fetch_yakka.py                ← 薬価CSV取得・JSON変換スクリプト
│
├── .github/
│   └── workflows/
│       └── update-yakka.yml          ← GitHub Actions定義ファイル
│
├── STRUCTURE.md                      ← 本ファイル（構造定義書）
└── README.md                         ← セットアップ手順・使い方（別途作成）
```

---

## 2. データフロー

```
【薬価データ生成フロー】

社会保険診療報酬支払基金
  └─ 医薬品マスターページ (https://www.ssk.or.jp/...)
       │
       │ スクレイピングでZIP URLを動的取得
       ↓
fetch_yakka.py
  ├─ ZIPダウンロード → 解凍
  ├─ CSV(Shift-JIS)パース（約13,000件）
  ├─ 薬価変換：int ÷ 100 → 円（小数2桁）
  ├─ 件数チェック（0件または前回比50%未満でエラー終了）
  └─ data/yakka.json として保存（UTF-8）

GitHub Actions（毎年4月・10月自動実行）
  └─ fetch_yakka.py を実行 → yakka.json をコミット・プッシュ

【アプリ参照フロー】

ブラウザ（index.html）
  ├─ DOMContentLoaded 時に fetch('./data/yakka.json')
  ├─ drugsData グローバル変数に格納
  ├─ 薬剤名入力（2文字以上）→ 300msデバウンス → searchDrugs()
  ├─ サジェスト選択 → 薬剤名・単位・薬価を自動入力
  ├─ 数量入力 → calcRow() → calcTotals() → updatePreview()
  └─ 印刷ボタン → バリデーション → window.print()
```

---

## 3. yakka.json スキーマ定義

```json
{
  "updated_at": "YYYY-MM-DD",
  "source": "社会保険診療報酬支払基金 医薬品マスター",
  "count": 13000,
  "drugs": [
    {
      "code": "610406079",
      "name": "ロキソニン錠60mg",
      "unit": "１錠",
      "price": 10.10
    }
  ]
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `updated_at` | string (YYYY-MM-DD) | データ取得日 |
| `source` | string | データ出典 |
| `count` | integer | 収録薬剤数 |
| `drugs[].code` | string | レセプト電算コード（列0） |
| `drugs[].name` | string | 医薬品名・規格名（列2） |
| `drugs[].unit` | string | 単位（列3） |
| `drugs[].price` | number | 薬価（円、小数2桁）。CSVの列8÷100 |

---

## 4. 医薬品マスターCSV 列マッピング

| 列番号 | 内容 | 例 | 変換処理 |
|--------|------|-----|---------|
| 0 | 医薬品コード | `610406079` | そのまま文字列 |
| 2 | 医薬品名・規格名 | `ロキソニン錠60mg` | そのまま |
| 3 | 単位 | `１錠` | そのまま |
| 8 | 薬価（0.1銭刻み整数） | `1010` | `round(int(v) / 100, 2)` → `10.10` |

---

## 5. JavaScript モジュール構成

### 5-1. 状態管理オブジェクト

```
AppState
├── headerData: {
│     date: string,          // 依頼日（YYYY-MM-DD）
│     docNumber: string,     // 書類番号
│     fromName: string,      // 依頼元薬局名
│     fromStaff: string,     // 依頼元担当者名
│     fromTel: string,       // 依頼元電話番号
│     toName: string,        // 依頼先薬局名
│     toStaff: string,       // 依頼先担当者名
│     note: string           // 備考
│   }
└── rows[]: {
      drugName: string,      // 薬剤名
      unit: string,          // 単位
      price: number,         // 薬価（円）
      quantity: number,      // 数量
      amount: number         // 金額（自動計算）
    }

drugsData: object | null     // fetch済みyakka.json（グローバル）
```

### 5-2. 関数一覧

| 関数名 | シグネチャ | 責務 |
|--------|-----------|------|
| `initApp` | `() → void` | 初期化・イベントリスナー登録・JSON読み込み起動 |
| `loadDrugsData` | `() → Promise<void>` | yakka.json を fetch で非同期ロード、失敗時トースト表示 |
| `loadPreset` | `() → void` | localStorage から依頼元情報を AppState.headerData に復元 |
| `savePreset` | `() → void` | AppState.headerData の依頼元3フィールドを localStorage に保存 |
| `addRow` | `() → void` | rows[] に空行を追加し updatePreview() を呼ぶ |
| `removeRow` | `(index: number) → void` | 指定インデックスの行を削除し再計算 |
| `updateRow` | `(index: number, field: string, val: any) → void` | 行データを更新して calcRow() → calcTotals() → updatePreview() |
| `searchDrugs` | `(keyword: string) → Drug[]` | drugsData.drugs を部分一致検索（最大10件） |
| `showSuggestions` | `(results: Drug[], inputEl: Element) → void` | サジェストドロップダウンを表示 |
| `hideSuggestions` | `() → void` | サジェストを非表示 |
| `selectSuggestion` | `(drug: Drug, index: number) → void` | サジェスト選択 → 行の薬剤名・単位・薬価を自動入力 |
| `calcRow` | `(index: number) → void` | `Math.floor(price × quantity)` で金額を計算 |
| `calcTotals` | `() → {subtotal, tax, total}` | 小計・消費税（10%、円未満切り捨て）・税込総額を計算 |
| `updatePreview` | `() → void` | プレビューエリアを全再描画 |
| `printDocument` | `() → void` | バリデーション後に window.print() を実行 |
| `resetAll` | `() → void` | 確認ダイアログ後にフォームを初期状態にリセット |
| `showToast` | `(message: string) → void` | 画面下部にトースト通知を表示（3秒後に自動消去） |
| `formatCurrency` | `(num: number) → string` | 数値を `"1,234"` 形式の文字列に変換 |
| `formatDateJP` | `(dateStr: string) → string` | `"2026-04-23"` → `"令和8年4月23日"` |

---

## 6. 自動計算ロジック

```
各行の金額  = Math.floor(薬価 × 数量)
小計（税抜）= Σ(各行の金額)
消費税額    = Math.floor(小計 × 0.10)
税込総額    = 小計 + 消費税額
```

- 薬価・数量いずれかが未入力（0またはNaN）の行は 0円として計算
- 数量の入力範囲：1〜9999（整数のみ）

---

## 7. 画面レイアウト（入力エリア構成）

### ヘッダー情報フィールド

| フィールド名 | input type | localStorage保存 | 初期値 |
|-------------|-----------|-----------------|--------|
| 依頼日 | `date` | - | 今日の日付 |
| 書類番号 | `text` | - | 空 |
| 依頼元薬局名 | `text` | ✅ | - |
| 依頼元担当者名 | `text` | ✅ | - |
| 依頼元電話番号 | `tel` | ✅ | - |
| 依頼先薬局名 | `text` | - | 空 |
| 依頼先担当者名 | `text` | - | 空 |
| 備考・メモ | `textarea` | - | 空 |

### 薬剤明細テーブル列

| 列名 | 入力種別 | 備考 |
|------|---------|------|
| No. | - | 自動採番（1始まり） |
| 薬剤名 | `text` + サジェスト | 2文字以上でドロップダウン表示 |
| 単位 | `text` | サジェスト選択時に自動入力、手動編集可 |
| 薬価（円/単位） | `number` | サジェスト選択時に自動入力、手動編集可 |
| 数量 | `number` | 1〜9999の整数 |
| 金額（円） | 読み取り専用 | 薬価×数量（自動計算） |
| 操作 | - | 行削除ボタン |

---

## 8. バリデーション仕様

| 条件 | 対応 |
|------|------|
| yakka.json 読み込み失敗 | トースト通知（薬価は手動入力可能） |
| 検索キーワード1文字以下 | サジェスト非表示 |
| 数量に文字入力 | `type="number"` で防止 |
| 薬剤明細0行で印刷実行 | アラート「薬剤が1件も登録されていません」 |
| 依頼元薬局名が空で印刷実行 | アラート「依頼元薬局名を入力してください」 |
| 薬価未入力行がある状態で印刷 | 確認ダイアログ「薬価が未入力の行があります。このまま印刷しますか？」 |

---

## 9. 印刷・CSS仕様

```css
@media print {
  .input-area, .btn-area, .nav-header { display: none !important; }
  .preview-area { width: 100%; }
  @page { size: A4 portrait; margin: 15mm; }
  tr { page-break-inside: avoid; }
}
```

- 印刷物下部に出典表示：「薬価は社会保険診療報酬支払基金 医薬品マスター（YYYY-MM-DD版）に基づきます。」
- 和暦変換：令和元年 = 2019年（2019-05-01〜）、令和基準年 = 2018年

---

## 10. GitHub Actions スケジュール

| cronトリガー | UTC | JST | 用途 |
|------------|-----|-----|------|
| `0 0 1 4 *` | 4月1日 00:00 | 4月1日 09:00 | 本改定（年1回） |
| `0 0 1 10 *` | 10月1日 00:00 | 10月1日 09:00 | 随時収載・中間改定 |
| `workflow_dispatch` | 手動 | 手動 | 緊急改定・初回セットアップ |

**必要な権限：** `permissions: contents: write`（yakka.json コミット・プッシュのため）

---

## 11. ホスティング・パス設定

| 項目 | 内容 |
|------|------|
| ホスト | GitHub Pages |
| URL | `https://<username>.github.io/yakka-app/` |
| fetch パス | `./data/yakka.json`（同一オリジン・CORSなし） |
| ソースブランチ | `main / (root)` |

---

## 12. データソース

| 項目 | 内容 |
|------|------|
| 提供機関 | 社会保険診療報酬支払基金 |
| ファイル形式 | ZIP（内部にCSV1ファイル）・Shift-JIS |
| 更新頻度 | 年2〜3回（薬価改定時） |
| 費用 | 無料・登録不要 |
| ZIP URL | 改定毎に変わる → スクレイピングで動的取得 |
