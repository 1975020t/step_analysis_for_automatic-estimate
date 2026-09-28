# 図面PDF 読み取り評価（pdf_data）

- 読み取り器: `golden.pdf_naive_baseline:NaiveTextReader`
- 図面数: 300（2026-09-28）
- 平均処理時間: 0.0 秒/図面、LLM呼び出し 0 回、入力 0 / 出力 0 トークン
- 正解データ照合: 一致（`pdf_v1.json`）

## 図面単位

| 指標 | 値 |
|---|---:|
| 価格項目がすべて正しい | 5.3% |
| 自動確定（価格項目がすべて正しく、要確認なし） | 5.3% |
| 危険誤答を含む図面 | 94.7% |

## 項目別

| 項目 | 正解率 | 危険誤答 | 要確認の割合 |
|---|---:|---:|---:|
| 材質 (`material`) | 50.0% | 50.0% | 0.0% |
| 板厚 (`thickness_mm`) | 44.0% | 56.0% | 0.0% |
| 数量 (`quantity`) | 57.3% | 42.7% | 0.0% |
| 表面処理 (`surface_treatment`) | 57.3% | 42.7% | 0.0% |
| 追加加工 (`processes`) | 34.0% | 66.0% | 0.0% |
| 特急 (`rush`) | 83.3% | 16.7% | 0.0% |
| 図番 (`drawing_no`) | 37.7% | 62.3% | 0.0% |
| 改訂 (`revision`) | 64.7% | 35.3% | 0.0% |
| 特記事項 (`flags`) | 69.3% | 30.7% | 0.0% |

## PDFの種類別（価格項目の正解率）

| 種類 | 図面数 | 材質 | 板厚 | 数量 | 表面処理 | 追加加工 | 特急 | 自動確定 | 危険誤答図面 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ベクター（CAD出力） | 150 | 88.7% | 62.7% | 83.3% | 83.3% | 41.3% | 93.3% | 10.7% | 89.3% |
| スキャン | 60 | 11.7% | 21.7% | 30.0% | 28.3% | 30.0% | 85.0% | 0.0% | 100.0% |
| FAX | 60 | 8.3% | 31.7% | 28.3% | 36.7% | 31.7% | 75.0% | 0.0% | 100.0% |
| 手書き・押印 | 30 | 16.7% | 20.0% | 40.0% | 26.7% | 10.0% | 46.7% | 0.0% | 100.0% |

## 様式別

| 様式 | 図面数 | 価格項目すべて正しい | 自動確定 |
|---|---:|---:|---:|
| bilingual | 45 | 4.4% | 4.4% |
| iso_en | 57 | 7.0% | 7.0% |
| jis_bom | 37 | 0.0% | 0.0% |
| jis_cad | 119 | 7.6% | 7.6% |
| memo | 42 | 2.4% | 2.4% |

## 書かれている場所別（正解率）

| 項目 | 場所 | 件数 | 正解率 |
|---|---|---:|---:|
| 材質 | bom | 21 | 38.1% |
| 材質 | free | 25 | 44.0% |
| 材質 | notes | 53 | 45.3% |
| 材質 | title | 162 | 43.2% |
| 追加加工 | bom | 13 | 0.0% |
| 追加加工 | callouts | 160 | 2.5% |
| 追加加工 | free | 12 | 16.7% |
| 追加加工 | notes | 79 | 7.6% |
| 数量 | bom | 21 | 0.0% |
| 数量 | free | 17 | 17.6% |
| 数量 | notes | 45 | 51.1% |
| 数量 | title | 124 | 42.7% |
| 特急 | free | 7 | 57.1% |
| 特急 | handwriting | 5 | 0.0% |
| 特急 | notes | 38 | 39.5% |
| 特急 | stamp | 10 | 0.0% |
| 特急 | title | 8 | 50.0% |
| 表面処理 | free | 30 | 33.3% |
| 表面処理 | notes | 62 | 33.9% |
| 表面処理 | title | 107 | 37.4% |
| 板厚 | free | 28 | 28.6% |
| 板厚 | notes | 45 | 48.9% |
| 板厚 | title | 145 | 22.1% |

## 危険誤答・失敗の例（284件中、先頭20件）

| 図面 | 種類 | 項目 | 読み取り | 正解 |
|---|---|---|---|---|
| Lv0_0000 | handwritten | 材質 | `null` | `"SPCC"` |
| Lv0_0000 | handwritten | 数量 | `null` | `10` |
| Lv0_0000 | handwritten | 表面処理 | `null` | `"BAKING_PAINT"` |
| Lv0_0000 | handwritten | 追加加工 | `[]` | `[{"code": "TAP_M3", "count_per_part": 5, "text": "5X M3x0.5 ` |
| Lv0_0000 | handwritten | 特急 | `false` | `true` |
| Lv0_0001 | vector | 追加加工 | `[]` | `[{"code": "COUNTERSINK", "count_per_part": 2, "text": "1-φ3.` |
| Lv0_0001 | vector | 特急 | `true` | `false` |
| Lv0_0002 | scan | 材質 | `null` | `"SPCC"` |
| Lv0_0002 | scan | 板厚 | `null` | `4.5` |
| Lv0_0002 | scan | 数量 | `null` | `50` |
| Lv0_0002 | scan | 表面処理 | `null` | `"ZINC_CLEAR"` |
| Lv0_0002 | scan | 追加加工 | `[]` | `[{"code": "UNREGISTERED", "count_per_part": 3, "text": "3X B` |
| Lv0_0003 | vector | 表面処理 | `null` | `"POWDER_COAT"` |
| Lv0_0003 | vector | 追加加工 | `[{"code": "TAP_M6", "count_per_part": 8}]` | `[{"code": "PRESS_NUT", "count_per_part": 5, "text": "クリンチナット` |
| Lv0_0004 | vector | 表面処理 | `"POWDER_COAT"` | `"NONE"` |
| Lv0_0005 | fax | 材質 | `null` | `"SPCC"` |
| Lv0_0005 | fax | 板厚 | `null` | `1.2` |
| Lv0_0005 | fax | 追加加工 | `[]` | `[{"code": "PRESS_NUT", "count_per_part": 4, "text": "PEMナット ` |
| Lv0_0006 | vector | 数量 | `null` | `30` |
| Lv0_0008 | scan | 材質 | `null` | `"SPHC"` |
| Lv0_0008 | scan | 板厚 | `null` | `2.3` |
| Lv0_0008 | scan | 表面処理 | `null` | `"BAKING_PAINT"` |
| Lv0_0008 | scan | 追加加工 | `[]` | `[{"code": "PRESS_NUT", "count_per_part": 5, "text": "圧入ナット M` |
| Lv0_0008 | scan | 特急 | `false` | `true` |
| Lv0_0009 | fax | 材質 | `null` | `"AL5052"` |
| Lv0_0009 | fax | 板厚 | `null` | `1.6` |
| Lv0_0009 | fax | 数量 | `null` | `300` |
| Lv0_0009 | fax | 表面処理 | `null` | `"NONE"` |
| Lv0_0009 | fax | 追加加工 | `[]` | `[{"code": "TAP_M4", "count_per_part": 1, "text": "1X M4-0.7 ` |
| Lv0_0011 | scan | 材質 | `null` | `"UNREGISTERED"` |
| Lv0_0011 | scan | 板厚 | `null` | `3.2` |
| Lv0_0011 | scan | 数量 | `null` | `1` |
| Lv0_0011 | scan | 表面処理 | `null` | `"POWDER_COAT"` |
| Lv0_0012 | fax | 材質 | `null` | `"SUS304"` |
| Lv0_0012 | fax | 板厚 | `null` | `1.2` |
| Lv0_0012 | fax | 数量 | `null` | `500` |
| Lv0_0012 | fax | 表面処理 | `null` | `"NONE"` |
| Lv0_0013 | vector | 板厚 | `4.0` | `null` |
| Lv0_0014 | vector | 追加加工 | `[{"code": "TAP_M4", "count_per_part": 7}]` | `[{"code": "COUNTERSINK", "count_per_part": 4, "text": "2-φ4.` |
| Lv0_0015 | fax | 材質 | `null` | `"SECC"` |
| Lv0_0015 | fax | 板厚 | `null` | `3.2` |
| Lv0_0015 | fax | 数量 | `null` | `100` |
| Lv0_0015 | fax | 表面処理 | `null` | `"POWDER_COAT"` |
| Lv0_0015 | fax | 追加加工 | `[]` | `[{"code": "PRESS_STUD", "count_per_part": 6, "text": "PEMスタッ` |
| Lv0_0016 | vector | 数量 | `null` | `2` |
| Lv0_0016 | vector | 追加加工 | `[{"code": "TAP_M3", "count_per_part": 1}]` | `[{"code": "COUNTERSINK", "count_per_part": 4, "text": "CSK F` |
| Lv0_0017 | scan | 材質 | `null` | `"UNREGISTERED"` |
| Lv0_0017 | scan | 板厚 | `null` | `3.2` |
| Lv0_0017 | scan | 数量 | `null` | `50` |
| Lv0_0017 | scan | 表面処理 | `null` | `"ZINC_CLEAR"` |
| Lv0_0017 | scan | 追加加工 | `[]` | `[{"code": "PRESS_NUT", "count_per_part": 6, "text": "クリンチナット` |
| Lv0_0018 | handwritten | 材質 | `null` | `"UNREGISTERED"` |
| Lv0_0018 | handwritten | 板厚 | `null` | `1.0` |
| Lv0_0018 | handwritten | 数量 | `null` | `300` |
| Lv0_0018 | handwritten | 表面処理 | `null` | `"UNREGISTERED"` |
| Lv0_0018 | handwritten | 追加加工 | `[]` | `[{"code": "TAP_M3", "count_per_part": 1, "text": "M3×0.5 タップ` |
| Lv0_0018 | handwritten | 特急 | `false` | `true` |
| Lv0_0019 | vector | 材質 | `"SPHC"` | `"SPCC"` |
| Lv0_0019 | vector | 板厚 | `null` | `2.3` |
| Lv0_0019 | vector | 追加加工 | `[]` | `[{"code": "UNREGISTERED", "count_per_part": 6, "text": "6X P` |
| Lv0_0021 | handwritten | 材質 | `null` | `"SUS304"` |
| Lv0_0021 | handwritten | 数量 | `null` | `2` |
| Lv0_0021 | handwritten | 表面処理 | `null` | `"NONE"` |
| Lv0_0021 | handwritten | 追加加工 | `[]` | `[{"code": "COUNTERSINK", "count_per_part": 5, "text": "5-φ4.` |
| Lv0_0022 | fax | 板厚 | `null` | `2.3` |
| Lv0_0022 | fax | 数量 | `null` | `10` |
| Lv0_0022 | fax | 表面処理 | `null` | `"ZINC_YELLOW"` |

## 受入条件

不合格:

- material accuracy 50.0% < 95%
- material dangerous 50.0% > 1%
- thickness_mm accuracy 44.0% < 95%
- thickness_mm dangerous 56.0% > 1%
- quantity accuracy 57.3% < 95%
- quantity dangerous 42.7% > 1%
- surface_treatment accuracy 57.3% < 95%
- surface_treatment dangerous 42.7% > 1%
- processes accuracy 34.0% < 95%
- processes dangerous 66.0% > 1%
- rush accuracy 83.3% < 95%
- rush dangerous 16.7% > 1%
- fax: material accuracy 8.3% < 90%
- fax: thickness_mm accuracy 31.7% < 90%
- fax: quantity accuracy 28.3% < 90%
- fax: surface_treatment accuracy 36.7% < 90%
- fax: processes accuracy 31.7% < 90%
- fax: rush accuracy 75.0% < 90%
- handwritten: material accuracy 16.7% < 90%
- handwritten: thickness_mm accuracy 20.0% < 90%
- handwritten: quantity accuracy 40.0% < 90%
- handwritten: surface_treatment accuracy 26.7% < 90%
- handwritten: processes accuracy 10.0% < 90%
- handwritten: rush accuracy 46.7% < 90%
- scan: material accuracy 11.7% < 90%
- scan: thickness_mm accuracy 21.7% < 90%
- scan: quantity accuracy 30.0% < 90%
- scan: surface_treatment accuracy 28.3% < 90%
- scan: processes accuracy 30.0% < 90%
- scan: rush accuracy 85.0% < 90%
- vector: material accuracy 88.7% < 90%
- vector: thickness_mm accuracy 62.7% < 90%
- vector: quantity accuracy 83.3% < 90%
- vector: surface_treatment accuracy 83.3% < 90%
- vector: processes accuracy 41.3% < 90%
- drawing_no accuracy 37.7% < 90%
- revision accuracy 64.7% < 90%
- flags accuracy 69.3% < 90%
- dangerous drawings 94.7% > 2%
- auto-confirmed 5.3% < 75%
