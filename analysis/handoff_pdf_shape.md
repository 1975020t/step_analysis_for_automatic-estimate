# 図面PDFだけからの見積（形状の概算）

STEP・DXF がなく図面PDFしかないときに、図面から形状の値（板厚・穴数・曲げ数・展開面積・切断長）を概算し、
既存の見積計算（マスターCSVとルール）に渡す。目標は **STEPから出した見積に対して金額の誤差±20%程度**。
結果は必ず概算（`status="partial"`、`reason_code="PDF_SHAPE_ESTIMATE"`）で、画面の「条件修正」で値を書き換えられる。

## 方式

金額の大部分は切断長（レーザー 3円/mm）と曲げ（500円/か所）で決まる。展開面積は材料費にしか効かず小さい。
そこで「切断長と曲げ数を外さない」ことに絞った。

1. **Claude に1回読ませる**（`src/pdf_shape.py`、既定 claude-sonnet-5）。返させるのは
   - 部品全体の外形寸法（図面に書かれた数字をそのまま。CAD出力のPDFではテキスト層にあるかを照合）
   - 展開図の位置（画像のピクセル座標）、投影図の範囲
   - 曲げ・穴の一覧（数ではなく1つずつ書き出させる）、図面の種類（部品か組立図か）
2. **展開図があれば画像で数える**（`src/pdf_flat_pattern.py`。LLMを使わない）。閉じた外形線を探し、
   その内側の閉じた輪郭を穴、外形を分ける直線（実線）と破線・一点鎖線の並びを曲げ線として数える。
   外形の外まで伸びる線（寸法線・引出線）は曲げ線にしない
3. **画像で数えられないとき**（展開図がない、FAX のようにノイズが多い、外形線が閉じていない）は、
   展開図か投影図の範囲を拡大して Claude にもう一度数えさせる
4. **式で面積と切断長を出す**（ルール）。a≥b≥c を外形寸法（平板は c=板厚）、n を曲げ数、h を穴数として
   - 切断長 = 2.279(a+b+c) + 23.636n + 38.436h − 55.351（下限 2(a+b)）
   - 展開面積 = 0.959ab + 0.373c(a+b) + 0.377c²

   係数は STEP の開発用データ（golden_data の500件）で最小二乗に当てた（`scripts/fit_pdf_shape_formula.py` で再現できる）。
   図面PDFのデータでは当てていない。正しい寸法と数を入れたときの金額の誤差は中央値約3%
5. 板厚は図面の読み取り（`PdfConditionReader`）か登録時の入力。金額は `QuoteEngine` が計算する（LLMは金額に関わらない）

信頼度：曲げ数・穴数は画像の計測が Claude の数と一致すれば medium、それ以外は low。面積は常に low。

## 画面・API

- 図面登録で PDF だけのとき、形状を概算して改訂の `analysis` に保存する。見積の作成時に未推定なら、そこで推定する。
  受付の3段階に合わせ、準備が計画に `shape` を入れ（`platform/drawings.py` の `shape_plan`）、抽出（`src/services/extraction.py`、
  データベースなし。AWS では抽出の Lambda）が読み取りのあとに推定し、保存が改訂に入れる（`store_shape`）
- 見積画面：状態「図面PDFから概算」、形状の表に「図面から推定」、条件修正の「寸法の値」欄に推定値を薄く表示し、上書きできる。
  確認事項に「形状の値は図面PDFからの概算です…誤差の目安は±20%」と、推定の根拠（画像で数えた／LLMが数えた）を出す
- API：`POST /api/drawings/shape-estimates`（`file_id`、`thickness_mm`）で受付、`GET /api/jobs/{id}` で `SheetMetalAnalysis`。
  APIキーがないときは 503 `SHAPE_ESTIMATOR_UNAVAILABLE`（画面では寸法を手入力する従来どおりの動き）
- 環境変数 `PDF_SHAPE_ESTIMATOR`：空（APIキーがあれば使う）、`off`、`recorded:<file.json>`（記録済みの結果を返す。お試しデータと資料の写真用、`docs/scripts/recorded_shape.json`）

## 精度の確かめ方

図面PDFのデータは、図面と同じ部品の STEP を持っている。**STEPの解析値で出した見積を正解**とし、図面PDFだけから出した見積との比を測る
（`scripts/evaluate_pdf_shape.py`。材質・表面処理・追加加工・特急は正解の条件で揃え、形状の違いだけを見る。板厚は与える）。

```
python scripts/evaluate_pdf_shape.py --data pdf_data --workers 8 --gate --report analysis/pdf_shape_eval_latest.md
python scripts/generate_pdf_golden.py --per-level 20 --seed 51 --unseen-ratio 0.2 --out pdf_shape_check
python scripts/evaluate_pdf_shape.py --data pdf_shape_check --workers 6 --gate --hide-examples --report analysis/pdf_shape_eval_seed51.md
```

合否の基準（`--gate`）：金額が±20%以内の図面が全体で90%以上、図面の種類（ベクター・スキャン・FAX・手書き）ごとに80%以上。

| データ | 枚数 | ±20%以内 | ±10%以内 | 誤差の中央値 | レポート |
|---|---:|---:|---:|---:|---|
| 開発用 pdf_data（seed=31） | 300 | 97.7% | 86.0% | 3.3% | `pdf_shape_eval_latest.md` |
| 開発に使っていない seed=51（未知の様式を2割含む） | 100 | 95.0% | 89.0% | 3.6% | `pdf_shape_eval_seed51.md` |

開発用での経過（±20%以内）：91.7%（最初）→ 92.3% → 96.0%（展開図の画像計測）→ 97.7%（FAXは拡大して数え直し）。
四角い外形の内側を穴と数える誤りを直したあと、穴数の一致は 82.7% → 90.7%（金額の±20%以内は変わらず 97.7%）。

## 限界

- 評価はすべて合成の図面。実際の図面（寸法の書き方、汚れ、複数部品、展開図の描き方）での精度は分からない
- 合成図面の生成器では、穴の引出し注記と実際の形状が一致しないことがある（注記の数と STEP の穴数が違う）。
  そのため注記ではなく展開図・投影図に描かれた穴を数えさせている
- 外形寸法の読み違いはそのまま切断長に効く。CAD出力のPDFはテキスト層との照合で検出するが、画像の図面では検出できない
- 1枚あたり約6,000入力・650出力トークン（Claude を1〜2回呼ぶ）。図面の画像を外部APIに送る
