# 引き継ぎ：展開図DXFの解析ロジック

## 最終ゴール

板金の **展開図DXF** を入力として、見積に使う値（展開面積・切断長・穴数・曲げ数）を、レイヤが整理されていない実務的なDXFでも正しく出し、**問題のあるDXF（開いた輪郭・複数部品）を確定値として出さない** 解析ロジックを作る。板厚はDXFに含まれないので、ユーザーの入力として受け取る。

達成方法（アルゴリズム、LLMの使い方、コード構成、作業順序）は任せる。途中の確認は不要。

## 受入条件

| 項目 | 条件 |
|---|---|
| 解析成功 | 変種 **D0・D1・D2 のそれぞれ** で **90%以上**。形状レベル **Lv0〜Lv4 のそれぞれ**（D0〜D2を合わせて）でも90%以上。成功＝展開面積・切断長の誤差 **±10%以内**、かつ穴数・曲げ数が正解と一致 |
| 危険誤答 | 変種ごとに **2%以下**。値が誤っているのに見積が「概算」にならないもの。**Xは確定（success）で返したら危険誤答** |
| 汎化 | ホールドアウト `dxf_holdout_v1`（100部品×4変種＝400ファイル）でも上の2条件を満たす |
| 回帰 | `python -m pytest -q` がすべてパスし、STEPのゴールデンデータ評価（`scripts/evaluate_golden.py --gate`）も合格のまま |
| 性能 | 1ファイルあたり平均2秒以内（LLMを使う場合は待ち時間込みで10秒以内） |
| UI | Streamlit画面で `.dxf` をアップロードし、板厚を入力して解析・見積まで表示できる |

判定コマンド:

```
python scripts/generate_dxf_golden.py --per-level 40 --seed 21 --out dxf_data
python scripts/evaluate_dxf.py --data dxf_data --analyzer src.dxf_analyzer:DxfAnalyzer --verify-frozen --gate --report analysis/dxf_eval_latest.md
```

最終判定（開発の最後に一度だけ）:

```
python scripts/generate_dxf_golden.py --per-level 20 --seed 22 --out dxf_holdout
python scripts/evaluate_dxf.py --data dxf_holdout --analyzer src.dxf_analyzer:DxfAnalyzer --verify-frozen golden/datasets/dxf_holdout_v1.json --gate --report analysis/dxf_eval_holdout.md
```

## インターフェース

- `src/dxf_analyzer.py` に `DxfAnalyzer` を作る（クラス名とモジュール名は評価コマンドに合わせる。変える場合はコマンドと資料も更新）
- 生成：`DxfAnalyzer(thickness_mm: float, k_factor: float = 0.33, k_factor_is_default: bool = True)`
- 実行：`.analyze(path) -> SheetMetalAnalysis`（`src/models.py`）。既存のフィールドと `status` の意味を守る
  - `success`：値が検算を通った（見積は確定）
  - `partial`：値は返すが確認が必要（見積は概算）
  - `unsupported`／`error`：値を返さない
- 問題のあるDXFは理由コードで区別する（例：`OPEN_CONTOUR`、`MULTIPLE_PARTS`、`NO_CUT_CONTOUR`）
- 単位は `$INSUNITS` に従い、結果は常にmm・mm²で返す

## 現状（参考）

素朴なベースライン（レイヤ名を信じるだけ。`golden/dxf_naive_baseline.py`）の結果。詳細は [dxf_eval_baseline.md](dxf_eval_baseline.md)、データの設計は [dxf_golden_design.md](dxf_golden_design.md)。

| 変種 | 解析成功 | 危険誤答 |
|---|---:|---:|
| D0 きれい | 100% | 0% |
| D1 注記あり | 100% | 0% |
| D2 乱雑 | 0% | 0% |
| X 確定してはいけない | — | 97% |

## ルールベース：初回の方向性（推奨。より良い方法があれば変えてよい）

1. **図形の正規化**：INSERT（ブロック参照）を分解し、LWPOLYLINE の円弧（bulge）、ARC、CIRCLE を扱い、`$INSUNITS` でmmに換算する。DIMENSION・TEXT・MTEXT は形状として扱わない（文字は板厚や材質の手がかりには使ってよい）
2. **端点をまとめる**：分割された線・重複・微小なすき間を、**端点どうしの距離**（例：0.02mm以内）でまとめる。座標を格子に丸める方式は、すき間が格子の境目をまたぐと閉じないので使わない（検証で確認済み）。重複線は取り除く
3. **線の役割を決める**：
   - レイヤ名に意味があれば使う（CUT／外形、BEND／曲げ線、DIM／寸法、…の英日の表記ゆれ）
   - 意味がなければ、線種（実線か、破線・一点鎖線・中心線か）、色、形で判断する。曲げ線は「実線でない直線で、両端が部品の外周上にある」、中心線は「穴の中心で交差する短い線」、図枠は「他のすべてを囲む大きな長方形で、内側に表題欄の格子と文字を持つ」
4. **部品を特定する**：切断線から閉じた領域を作り、図枠や表題欄の枠を除いた、材料のある領域を部品とする。穴＝部品の内側の閉じた輪郭
5. **問題を検出する**：部品の外形になり得る閉じた領域が2つ以上ある → `MULTIPLE_PARTS`。外形が閉じない（0.3mm程度以上の開き）→ `OPEN_CONTOUR`。どちらも確定で返さない
6. **自己検算で status を決める**：外形が1つの単純な閉じた形、穴がすべて外形の内側で互いに重ならない、曲げ線の両端が外形上にある、寸法（DIMENSION の実測値）と外形の大きさが矛盾しない、などを満たしたときだけ `success`

## LLM（Claude API）の利用

使ってよい（任意）。キーは環境変数 `ANALYSIS_ANTHROPIC_API_KEY`（クラウド環境に設定済み。`ANTHROPIC_API_KEY` や `.env` でも可）。疎通確認は `python scripts/check_claude_api.py`。使いどころの例：表題欄の文字（板厚・材質・数量）の読み取り、レイヤ名の意味の推定。ただし **数値（面積・長さ・個数）はLLMに出させず、幾何計算と検算で決める**。pytest ではAPIを呼ばない（記録済みの応答か偽のクライアントを使う）。LLMを使った場合は、ルールベースのみとの比較（成功率・処理時間・トークン数）を残す。

## 制約

- **正解データを変えて合格させない**：`golden/sheetgen.py`、`golden/sampler.py`、`golden/dxf_golden.py`、`golden/truth_v2.py`、`golden/datasets/` は変更しない。生成器の不具合を見つけた場合は、新しい版（例：`dxf_v2`）を作り、既存の版は残して報告する
- **ホールドアウトで調整しない**：`golden/datasets/dxf_holdout_v1.json` は開かない。seed=22 のデータは最終判定のときだけ生成する
- **STEPの解析を壊さない**：`src/sheetmetal_*.py` を変更する場合は、STEPのゴールデン評価で合格のままであることを確認する
- **金額はLLMに計算させない**
- 評価レポートの数字はハーネスの出力をそのまま使い、推測で書かない

## 完了時に残すもの

- 改修したコードとテスト
- `analysis/dxf_eval_latest.md`（開発データ）と `analysis/dxf_eval_holdout.md`（ホールドアウト）
- README の「解析方式」へのDXFの説明の追記
- LLMを使った場合は、その比較結果

## 対象外（今回はやらない）

- 三面図のDXF（図面PDFと同様に後で扱う）
- DWG（DXFへの変換ツールの選定は別途。ライセンス確認が必要）
- 1ファイル複数部品を部品ごとに分けて見積る機能（今回は検出して確定しないところまで）
- 曲げ線のない展開図からの曲げ数の推定
