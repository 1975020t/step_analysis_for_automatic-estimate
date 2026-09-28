# 図面PDF 読み取り評価（pdf_holdout）

- 読み取り器: `src.pdf_reader:PdfConditionReader`
- 図面数: 200（2026-09-28）
- 平均処理時間: 1.5 秒/図面、LLM呼び出し 222 回、入力 2,648,437 / 出力 135,200 トークン
- 正解データ照合: 一致（`pdf_holdout_v1.json`）


> **注意：この判定は未完了です（2026-09-28）。** 200枚のうち52枚（vector 25・scan 11・fax 10・handwritten 6、うち開発用にない様式9枚）が、
> Claude APIのクレジット残高不足（HTTP 400）で読み取れていない。下の表の約73%は、この52枚を不正解として数えた値で、読み間違いによるものではない。
> 読み取れた148枚の応答はキャッシュ済み。クレジット追加後に同じコマンドを再実行すると、残り52枚だけAPIを呼び、この判定を完了できる（同じデータ・同じコードでの一回の判定の続き）。
> ホールドアウトの個別の誤りは見ておらず、ホールドアウトに合わせた調整もしていない。

### 参考：読み取れた148枚だけの集計（正式な判定ではない）

| 対象 | 枚数 | 材質 | 板厚 | 数量 | 表面処理 | 追加加工 | 特急 | 自動確定 | 危険誤答の図面 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 読み取れた全図面 | 148 | 98.0% | 99.3% | 98.6% | 95.3% | 100.0% | 98.6% | 90.5% | 0 |
| vector | 75 | 98.7% | 98.7% | 100.0% | 100.0% | 100.0% | 100.0% | 97.3% | 0 |
| scan | 29 | 96.6% | 100.0% | 100.0% | 100.0% | 100.0% | 96.6% | 93.1% | 0 |
| fax | 30 | 96.7% | 100.0% | 96.7% | **76.7%** | 100.0% | 100.0% | 70.0% | 0 |
| handwritten | 14 | 100.0% | 100.0% | 92.9% | 100.0% | 100.0% | 92.9% | 92.9% | 0 |
| 開発用にない様式 | 28 | 96.4% | 100.0% | 100.0% | 92.9% | 100.0% | 96.4% | 85.7% | 0 |

図番 147/148、改訂 148/148、特記事項 146/148 が正解。
読み取れた範囲でも **FAX図面の表面処理（76.7%）が種類別の条件（90%以上）を下回る**。不足分の7枚はすべて「値は正しいが要確認になった」もので、誤った値はない（危険誤答0）。2回の読み取りが一致しなかったため人の確認に回った。

## 図面単位

| 指標 | 値 |
|---|---:|
| 価格項目がすべて正しい | 73.0% |
| 自動確定（価格項目がすべて正しく、要確認なし） | 67.0% |
| 危険誤答を含む図面 | 0.0% |

## 項目別

| 項目 | 正解率 | 危険誤答 | 要確認の割合 |
|---|---:|---:|---:|
| 材質 (`material`) | 73.0% | 0.0% | 1.5% |
| 板厚 (`thickness_mm`) | 74.0% | 0.0% | 0.5% |
| 数量 (`quantity`) | 74.0% | 0.0% | 1.0% |
| 表面処理 (`surface_treatment`) | 74.0% | 0.0% | 3.5% |
| 追加加工 (`processes`) | 74.0% | 0.0% | 0.0% |
| 特急 (`rush`) | 74.0% | 0.0% | 1.0% |
| 図番 (`drawing_no`) | 73.5% | 0.5% | 0.0% |
| 改訂 (`revision`) | 74.0% | 0.0% | 0.0% |
| 特記事項 (`flags`) | 73.0% | 1.0% | 0.0% |

## PDFの種類別（価格項目の正解率）

| 種類 | 図面数 | 材質 | 板厚 | 数量 | 表面処理 | 追加加工 | 特急 | 自動確定 | 危険誤答図面 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ベクター（CAD出力） | 100 | 74.0% | 75.0% | 75.0% | 75.0% | 75.0% | 75.0% | 73.0% | 0.0% |
| スキャン | 40 | 72.5% | 72.5% | 72.5% | 72.5% | 72.5% | 72.5% | 67.5% | 0.0% |
| FAX | 40 | 72.5% | 75.0% | 75.0% | 75.0% | 75.0% | 75.0% | 52.5% | 0.0% |
| 手書き・押印 | 20 | 70.0% | 70.0% | 70.0% | 70.0% | 70.0% | 70.0% | 65.0% | 0.0% |

## 様式別

| 様式 | 図面数 | 価格項目すべて正しい | 自動確定 |
|---|---:|---:|---:|
| bilingual | 29 | 82.8% | 79.3% |
| iso_en | 39 | 66.7% | 61.5% |
| jis_bom | 32 | 75.0% | 65.6% |
| jis_cad | 60 | 65.0% | 58.3% |
| memo | 20 | 85.0% | 85.0% |
| spec_sheet | 20 | 80.0% | 70.0% |

## 開発用にない様式（ホールドアウトのみ）

| 区分 | 図面数 | 価格項目すべて正しい | 自動確定 |
|---|---:|---:|---:|
| 開発用と同じ様式 | 163 | 72.4% | 67.5% |
| 開発用にない様式 | 37 | 75.7% | 64.9% |

## 書かれている場所別（正解率）

| 項目 | 場所 | 件数 | 正解率 |
|---|---|---:|---:|
| 材質 | bom | 16 | 87.5% |
| 材質 | free | 15 | 86.7% |
| 材質 | notes | 40 | 72.5% |
| 材質 | title | 112 | 68.8% |
| 追加加工 | bom | 14 | 71.4% |
| 追加加工 | callouts | 109 | 77.1% |
| 追加加工 | free | 5 | 80.0% |
| 追加加工 | notes | 44 | 68.2% |
| 数量 | bom | 8 | 87.5% |
| 数量 | free | 8 | 87.5% |
| 数量 | notes | 34 | 79.4% |
| 数量 | title | 85 | 72.9% |
| 特急 | free | 3 | 100.0% |
| 特急 | handwriting | 5 | 60.0% |
| 特急 | notes | 23 | 73.9% |
| 特急 | stamp | 5 | 80.0% |
| 特急 | title | 9 | 88.9% |
| 表面処理 | free | 17 | 88.2% |
| 表面処理 | notes | 51 | 78.4% |
| 表面処理 | title | 67 | 70.1% |
| 板厚 | free | 15 | 100.0% |
| 板厚 | notes | 36 | 63.9% |
| 板厚 | title | 92 | 77.2% |

## 危険誤答・失敗の例（52件中、先頭20件）

| 図面 | 種類 | 項目 | 読み取り | 正解 |
|---|---|---|---|---|
| Lv3_0025 | scan | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SECC"` |
| Lv3_0025 | scan | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `2.0` |
| Lv3_0025 | scan | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `300` |
| Lv3_0025 | scan | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0025 | scan | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "TAP_M5", "count_per_part": 7, "text": "M5×0.8 タップ` |
| Lv3_0025 | scan | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv3_0028 | handwritten | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SPCC"` |
| Lv3_0028 | handwritten | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `2.3` |
| Lv3_0028 | handwritten | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `50` |
| Lv3_0028 | handwritten | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"POWDER_COAT"` |
| Lv3_0028 | handwritten | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[]` |
| Lv3_0028 | handwritten | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `true` |
| Lv3_0030 | handwritten | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"AL5052"` |
| Lv3_0030 | handwritten | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0030 | handwritten | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0030 | handwritten | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"NONE"` |
| Lv3_0030 | handwritten | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "PRESS_STUD", "count_per_part": 6, "text": "PEMスタッ` |
| Lv3_0030 | handwritten | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv3_0031 | vector | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SECC"` |
| Lv3_0031 | vector | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0031 | vector | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `50` |
| Lv3_0031 | vector | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"POWDER_COAT"` |
| Lv3_0031 | vector | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[]` |
| Lv3_0031 | vector | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv3_0032 | vector | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SPHC"` |
| Lv3_0032 | vector | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `2.3` |
| Lv3_0032 | vector | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0032 | vector | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"NONE"` |
| Lv3_0032 | vector | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "TIG_WELD", "count_per_part": 4, "text": "コーナー4ヶ所 ` |
| Lv3_0032 | vector | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv3_0033 | vector | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SUS304"` |
| Lv3_0033 | vector | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0033 | vector | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0033 | vector | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0033 | vector | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "PRESS_NUT", "count_per_part": 4, "text": "クリンチナット` |
| Lv3_0033 | vector | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv3_0034 | scan | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SUS304"` |
| Lv3_0034 | scan | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `2.0` |
| Lv3_0034 | scan | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0034 | scan | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"NONE"` |
| Lv3_0034 | scan | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "TIG_WELD", "count_per_part": 3, "text": "角部 TIG溶接` |
| Lv3_0034 | scan | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv3_0035 | vector | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"UNREGISTERED"` |
| Lv3_0035 | vector | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `3.2` |
| Lv3_0035 | vector | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `2000` |
| Lv3_0035 | vector | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"NONE"` |
| Lv3_0035 | vector | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[]` |
| Lv3_0035 | vector | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `true` |
| Lv3_0036 | vector | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SPCC"` |
| Lv3_0036 | vector | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0036 | vector | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0036 | vector | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"ZINC_CLEAR"` |
| Lv3_0036 | vector | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "TAP_M6", "count_per_part": 6, "text": "M6タップ 6ヶ所"` |
| Lv3_0036 | vector | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `true` |
| Lv3_0037 | fax | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"AL5052"` |
| Lv3_0037 | fax | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `0.8` |
| Lv3_0037 | fax | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `300` |
| Lv3_0037 | fax | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"UNREGISTERED"` |
| Lv3_0037 | fax | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[]` |
| Lv3_0037 | fax | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv3_0038 | fax | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SUS304"` |
| Lv3_0038 | fax | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `1.6` |
| Lv3_0038 | fax | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `50` |
| Lv3_0038 | fax | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"POWDER_COAT"` |
| Lv3_0038 | fax | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "PRESS_NUT", "count_per_part": 5, "text": "圧入ナット M` |
| Lv3_0038 | fax | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv3_0039 | scan | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SECC"` |
| Lv3_0039 | scan | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `3.2` |
| Lv3_0039 | scan | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv3_0039 | scan | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"UNREGISTERED"` |
| Lv3_0039 | scan | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "SPOT_WELD", "count_per_part": 12, "text": "スポット溶接` |
| Lv3_0039 | scan | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv4_0000 | fax | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SPHC"` |
| Lv4_0000 | fax | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `2.0` |
| Lv4_0000 | fax | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv4_0000 | fax | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"POWDER_COAT"` |
| Lv4_0000 | fax | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "PRESS_NUT", "count_per_part": 5, "text": "クリンチナット` |
| Lv4_0000 | fax | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv4_0001 | vector | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SUS304"` |
| Lv4_0001 | vector | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `1.2` |
| Lv4_0001 | vector | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `2` |
| Lv4_0001 | vector | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"NONE"` |
| Lv4_0001 | vector | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "PRESS_NUT", "count_per_part": 3, "text": "クリンチナット` |
| Lv4_0001 | vector | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv4_0002 | vector | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"UNREGISTERED"` |
| Lv4_0002 | vector | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `3.2` |
| Lv4_0002 | vector | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `1` |
| Lv4_0002 | vector | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv4_0002 | vector | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[]` |
| Lv4_0002 | vector | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv4_0003 | vector | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv4_0003 | vector | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `3.2` |
| Lv4_0003 | vector | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `500` |
| Lv4_0003 | vector | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv4_0003 | vector | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "TAP_M5", "count_per_part": 3, "text": "3X M5-0.8 ` |
| Lv4_0003 | vector | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `true` |
| Lv4_0004 | vector | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"UNREGISTERED"` |
| Lv4_0004 | vector | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv4_0004 | vector | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `50` |
| Lv4_0004 | vector | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv4_0004 | vector | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[]` |
| Lv4_0004 | vector | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv4_0005 | vector | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SUS304"` |
| Lv4_0005 | vector | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv4_0005 | vector | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv4_0005 | vector | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"POWDER_COAT"` |
| Lv4_0005 | vector | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "TAP_M4", "count_per_part": 3, "text": "3X M4-0.7 ` |
| Lv4_0005 | vector | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |
| Lv4_0006 | handwritten | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SPCC"` |
| Lv4_0006 | handwritten | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv4_0006 | handwritten | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `2` |
| Lv4_0006 | handwritten | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"NONE"` |
| Lv4_0006 | handwritten | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "PRESS_NUT", "count_per_part": 4, "text": "SELF-CL` |
| Lv4_0006 | handwritten | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `true` |
| Lv4_0007 | scan | 材質 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `"SPCC"` |
| Lv4_0007 | scan | 板厚 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `4.5` |
| Lv4_0007 | scan | 数量 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `20` |
| Lv4_0007 | scan | 表面処理 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `null` |
| Lv4_0007 | scan | 追加加工 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `[{"code": "COUNTERSINK", "count_per_part": 4, "text": "皿もみ M` |
| Lv4_0007 | scan | 特急 | `BadRequestError: Error code: 400 - {'type': 'error', 'error'` | `false` |

## 受入条件

不合格:

- material accuracy 73.0% < 95%
- thickness_mm accuracy 74.0% < 95%
- quantity accuracy 74.0% < 95%
- surface_treatment accuracy 74.0% < 95%
- processes accuracy 74.0% < 95%
- rush accuracy 74.0% < 95%
- fax: material accuracy 72.5% < 90%
- fax: thickness_mm accuracy 75.0% < 90%
- fax: quantity accuracy 75.0% < 90%
- fax: surface_treatment accuracy 75.0% < 90%
- fax: processes accuracy 75.0% < 90%
- fax: rush accuracy 75.0% < 90%
- handwritten: material accuracy 70.0% < 90%
- handwritten: thickness_mm accuracy 70.0% < 90%
- handwritten: quantity accuracy 70.0% < 90%
- handwritten: surface_treatment accuracy 70.0% < 90%
- handwritten: processes accuracy 70.0% < 90%
- handwritten: rush accuracy 70.0% < 90%
- scan: material accuracy 72.5% < 90%
- scan: thickness_mm accuracy 72.5% < 90%
- scan: quantity accuracy 72.5% < 90%
- scan: surface_treatment accuracy 72.5% < 90%
- scan: processes accuracy 72.5% < 90%
- scan: rush accuracy 72.5% < 90%
- vector: material accuracy 74.0% < 90%
- vector: thickness_mm accuracy 75.0% < 90%
- vector: quantity accuracy 75.0% < 90%
- vector: surface_treatment accuracy 75.0% < 90%
- vector: processes accuracy 75.0% < 90%
- vector: rush accuracy 75.0% < 90%
- drawing_no accuracy 73.5% < 90%
- revision accuracy 74.0% < 90%
- flags accuracy 73.0% < 90%
- auto-confirmed 67.0% < 75%
- unseen layouts: material accuracy 75.7% < 90%
- unseen layouts: thickness_mm accuracy 75.7% < 90%
- unseen layouts: quantity accuracy 75.7% < 90%
- unseen layouts: surface_treatment accuracy 75.7% < 90%
- unseen layouts: processes accuracy 75.7% < 90%
- unseen layouts: rush accuracy 75.7% < 90%
