# CLAUDE.md

STEP形式の板金部品を解析し、ルールベースで見積を出すシステムのバックエンド（デモUIはStreamlit）。
現在の主タスクは **解析ロジックの改善**。最終ゴールと受入条件は [analysis/handoff_analysis_logic.md](analysis/handoff_analysis_logic.md) にある。着手前に読むこと。
進め方・設計は任されている。途中で確認を取らずに、受入条件を満たすまで進めてよい。

## 環境

- Python 3.11 で動作確認済み（CadQuery の都合で 3.10〜3.12）。依存は `requirements.txt`
- ユーザーは Windows / PowerShell。README のコマンドは `.venv\Scripts\python.exe` 形式
- 秘密情報は `.env`（Git管理外、雛形は `.env.example`）。プッシュ前に `python scripts/check_secrets.py --history`

## よく使うコマンド

```
python -m pytest -q                                                      # 全テスト（全件パスが前提）
python scripts/generate_golden.py --per-level 100 --seed 1 --out golden_data     # 開発用データ508件（約1分）
python scripts/evaluate_golden.py --data golden_data --verify-frozen --tolerance 0.10 --gate \
    --report analysis/golden_eval_latest.md                              # 評価（約2分）。--gate で合否
python scripts/evaluate_golden.py --data golden_data --tolerance 0.10 --levels Lv2 --limit 20   # 部分的な素早い確認
python scripts/render_golden.py --data golden_data --per-level 3          # 形状と判定の一覧画像
python scripts/check_claude_api.py                                        # Claude APIキーの疎通確認
```

部品ごとの結果は `golden_data/results.csv`（誤差・理由コード・展開方式・LLMトークン数）。失敗の分析はまずここから。

## コード構成

- `src/sheetmetal_analyzer.py` 解析の司令塔。出力は `src/models.py` の `SheetMetalAnalysis`
- `src/sheetmetal_recognition.py` 板厚・表裏ペア・曲げの認識
- `src/sheetmetal_unfold.py` 展開
- `src/sheetmetal_geometry.py` 公差、面隣接グラフ、2Dループ処理
- `src/quote_engine.py` 見積計算。`is_estimate`（概算表示）は解析結果の status / 信頼度から決まる
- `src/claude_api.py` Claude APIクライアント（応答キャッシュ、トークン集計）
- `src/llm_assisted_analyzer.py` LLMを使う解析器のサンプル（`--analyzer` で評価できる）
- `golden/` ゴールデンデータ生成器、`golden/datasets/golden_v1.json` が開発用の凍結正解値（508件）、`holdout_v1.json` が最終判定用（200件、開発中は開かない）
- `scripts/evaluate_golden.py` 評価ハーネス
- `analysis/prototypes/unfold_prototype.py` 汎用展開の参考実装（本番コードではない。方向性は引き継ぎ資料の「ルールベース：初回の改善方向」）

## 守ること

1. **正解データを変えて合格させない。** `golden/sheetgen.py`・`golden/sampler.py`・`golden/datasets/` は変更しない。生成器の不具合を見つけたら `golden_v2` を新設し、`golden_v1` は残して報告する
2. **ホールドアウトで調整しない。** 正解値 `golden/datasets/holdout_v1.json` は開かない。seed=2 のデータは最終判定のときだけ生成する
3. **金額をLLMに計算させない。** 金額はマスターCSVとルールで決定論的に計算する
4. **LLMの出力をそのまま数値に使わない。** 数値を出す場合は幾何計算で検算する。形状データをClaude APIへ送ることは承認済み
5. **pytest からAPIを呼ばない。** 記録済みの応答か偽のクライアントを使う
6. `SheetMetalAnalysis` の既存フィールドと `status` の意味を壊さない（UIと見積が依存している）
7. 変えたら `pytest` と評価ハーネスを両方回し、レポートの数字で改善を示す。数字は推測で書かない
