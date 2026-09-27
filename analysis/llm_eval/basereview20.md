# ゴールデンデータ評価レポート

- データ: seed=1, 各レベル100件＋手作り（100件）× 繰り返し1回
- 解析器: `src.llm_assisted_analyzer:LLMAssistedAnalyzer`
- 判定基準: 板厚・展開面積・切断長の誤差 ±10%以内、曲げ数は完全一致、穴数は完全一致、ヘム（170°以上の曲げ）の検出数も一致
- K=0.5（正解データと同値を「指定済み加工条件」として解析器へ渡す）
- 「概算」判定は QuoteEngine.is_estimate をそのまま使用

## レベル別サマリ

| レベル | 件数 | **解析成功** | 確定で正解 | 正解だが概算 | 誤り（概算表示あり） | **危険誤答** | 解析不可 | 平均処理時間 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Lv0 | 20 | **100%** | 100% | 0% | 0% | **0%** | 0% | 5.1s |
| Lv1 | 20 | **40%** | 0% | 40% | 60% | **0%** | 0% | 7.8s |
| Lv2 | 20 | **10%** | 10% | 0% | 45% | **45%** | 0% | 7.0s |
| Lv3 | 20 | **10%** | 0% | 10% | 70% | **20%** | 0% | 8.4s |
| Lv4 | 20 | **40%** | 0% | 40% | 45% | **0%** | 15% | 7.3s |

## 値を返したケースの誤差（|誤差|の中央値 / 95パーセンタイル）

| レベル | 面積 | 切断長 | 穴数一致 | 曲げ数一致 | ヘム検出一致 |
|---|---:|---:|---:|---:|---:|
| Lv0 | 0.0% / 0.0% | 0.0% / 0.0% | 100% | 100% | 100% |
| Lv1 | 0.0% / 0.0% | 0.0% / 0.0% | 40% | 100% | 100% |
| Lv2 | 0.0% / 0.0% | 8.6% / 20.4% | 15% | 100% | 100% |
| Lv3 | 12.1% / 23.1% | 20.9% / 32.3% | 20% | 100% | 100% |
| Lv4 | 0.0% / 0.0% | 0.0% / 0.0% | 47% | 100% | 100% |

## 解析不可・概算の理由コード

| レベル | 理由コード（件数） |
|---|---|
| Lv0 | - |
| Lv1 | INTERNAL_BOUNDARY_ESTIMATED (18) |
| Lv2 | LLM_REVIEW_FLAGGED (7), FLAT_PATTERN_ESTIMATED (1) |
| Lv3 | LLM_REVIEW_FLAGGED (12), FLAT_PATTERN_ESTIMATED (3) |
| Lv4 | INTERNAL_BOUNDARY_ESTIMATED (11), LLM_REVIEW_FLAGGED (4), EXCEPTION:BadRequestError (3) |

## 危険誤答の例（誤差の大きい順、全13件中 上位10件）

| 部品 | 形状 | 展開方式 | 面積誤差 | 切断長誤差 | 穴 解析/正解 | 曲げ 解析/正解 |
|---|---|---|---:|---:|---:|---:|
| Lv3_0016 | 多段フランジ | geometric_branched_tray_unfold | -14.9% | -26.9% | 6/10 | 7/7 |
| Lv2_0017 | 底面＋1段フランジ | geometric_branched_tray_unfold | +0.0% | -20.4% | 2/7 | 4/4 |
| Lv2_0008 | 底面＋1段フランジ | geometric_branched_tray_unfold | +0.0% | -19.6% | 1/7 | 3/3 |
| Lv3_0006 | 多段フランジ | geometric_branched_tray_unfold | -16.8% | -17.4% | 3/5 | 4/4 |
| Lv2_0018 | 底面＋1段フランジ | geometric_branched_tray_unfold | +0.0% | -16.5% | 4/8 | 2/2 |
| Lv3_0007 | 多段フランジ | geometric_branched_tray_unfold | -16.4% | -14.4% | 6/9 | 4/4 |
| Lv3_0008 | 多段フランジ | geometric_branched_tray_unfold | -12.4% | -14.7% | 5/8 | 5/5 |
| Lv2_0000 | 底面＋1段フランジ | geometric_branched_tray_unfold | +0.0% | -10.7% | 6/8 | 3/3 |
| Lv2_0013 | 底面＋1段フランジ | geometric_branched_tray_unfold | +0.0% | -8.6% | 4/7 | 2/2 |
| Lv2_0012 | 底面＋1段フランジ | geometric_branched_tray_unfold | +0.0% | -7.9% | 3/6 | 2/2 |

## LLM利用量

- モデル: claude-sonnet-5
- API呼び出し 97回（キャッシュ再利用 0回）、入力 148,242 トークン、出力 45,078 トークン
- 1件あたり平均: 入力 1,528 / 出力 465 トークン

## 結果区分の定義

- **解析成功**: 値が許容誤差内（「確定で正解」＋「正解だが概算」）
- **確定で正解**: 許容誤差内、かつ見積が「概算」表示にならない（目指す状態）
- **正解だが概算**: 値は正しいが「概算」表示になる（保守的すぎる）
- **誤り（概算表示あり）**: 値が誤っているが「概算」表示で担当者に注意が促される
- **危険誤答**: 値が誤っているのに「概算」表示にならない（誤った金額が確定値として出る）
- **解析不可**: unsupported / error / タイムアウト（金額は出ない）
- ヘム（Lv4）は解析対象。180°前後の曲げを bend_evidence で報告できていない場合は誤りとして扱う
- --must-reject で指定したレベルは「解析不可」が正しい結果で、値を返した場合は誤りとして扱う

再現手順:

```
python scripts/evaluate_golden.py --data /home/user/step_analysis_for_automatic-estimate/golden_data --tolerance 0.10 --limit 20 --workers 4 --csv /tmp/claude-0/basereview20.csv --report /tmp/claude-0/basereview20.md --analyzer src.llm_assisted_analyzer:LLMAssistedAnalyzer
```
