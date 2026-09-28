# 引き継ぎ（追加）：図面PDF読み取りの懸念点への対応

PR #11（`PdfConditionReader`）のレビューで見つかった懸念点への対応。元の引き継ぎは [handoff_pdf.md](handoff_pdf.md)。

## 背景（レビューで分かったこと）

1. **ホールドアウトの判定が終わっていない**。200枚のうち52枚がAPIのクレジット不足で未読。読めた148枚でも、FAX図面の表面処理が76.7%で、種類別の条件（90%以上）を満たさない（FAXは40枚なので、残り10枚がすべて正しくても最大82.5%）
2. **レポートの説明と数字が合っていない**。「不足分の7枚はすべて値は正しいが要確認」とあるが、ハーネスの正解率は要確認でも値が正しければ正解に数える定義なので、その説明なら76.7%にはならない
3. **プロンプトとルールが合成データの語彙に寄っている**。プロンプトに生成器の紛らわしい語句（「月産500個予定（参考）」「相手部品：A5052」「急ぎません」など）が例として入り、ルールには合成データのためだけの語（わざと入れた誤字「紛体」、部品表の金具の材質「SWCH」など）が入っている。開発用データとホールドアウトは同じ語彙なので、**知らない書き方への強さはどちらのデータでも測れていない**
4. **知らない語が来たときに安全側に倒れるかが未検証**。語彙をずらした開発用データの語句を今のルール（`src/pdf_terms.py`）だけに通すと、「特急不要」「急ぎではありません」を特急と判定するなど、確定で誤りうる経路がある（LLMの判断と組み合わせたときの結果は未測定）
5. **ホールドアウトのレポートに正解値が出ている**（失敗例の表）。このため `pdf_holdout_v1` は、今後の最終判定には使えない

## 最終ゴール

**知らない書き方（語彙）が来ても、黙って間違った値を確定しない**読み取り器にする。知らない語は「要確認」か「未登録」に倒れてよい（自動確定が下がるのは許容）。プロンプトとルールは、合成データ固有の言い回しに頼らない一般的な記述にする。開発用データ `pdf_v1` での受入条件は満たしたままにする。

達成方法は任せる。途中の確認は不要。

## 語彙をずらしたデータ

同じ部品生成器・同じ正解規則で、**言い回しだけを別の語彙に置き換えた**図面（`golden/vocab/*.json`、生成は `--vocab`）。

| データ | 用途 | 図面数 | 正解値（凍結） | 生成コマンド |
|---|---|---:|---|---|
| pdf_shift_dev | 開発用（見てよい、失敗を分析してよい） | 100 | `golden/datasets/pdf_shift_dev_v1.json` | `python scripts/generate_pdf_golden.py --per-level 20 --seed 41 --vocab golden/vocab/shift_dev.json --out pdf_shift_dev` |
| pdf_shift_blind | 判定用（**リポジトリにない**。完了後にユーザー側で評価する） | 100 | リポジトリ外 | — |

語彙の例（shift_dev）：材質「SECC-20/20」「A5052-H34」、未登録材質「SPCE」「A6061-T6」、表面処理「パウダーコート（白）」「三価クロメート処理（クリア）」、処理なし「処理不要」、加工「M4 めねじ 4ヶ所」「セルフクリンチングナット M4」、未登録の加工「ハーフパンチ」「ルーバー」、特急「最短納期でお願いします」、特急でないもの「特急不要」「納期は通常で可」。

## 作業と受入条件

| # | 作業 | 受入条件 |
|---|---|---|
| 0 | **変更前の記録**（PR #11 のコードのまま） | `pdf_holdout_v1` の判定を最後まで回してレポートを完成させる（前回の応答キャッシュが残っていれば未読の52枚だけ、なければ200枚分APIを呼ぶ）。同じコードで `pdf_shift_dev` も回し、変更前の値として残す |
| 1 | 知らない語への安全性 | `pdf_shift_dev` で安全性の判定（`--gate-profile safety`）に合格：価格項目ごとの危険誤答 **1%以下**、危険誤答を含む図面 **2%以下**。正解率・自動確定はレポートに残す（参考） |
| 2 | プロンプトの一般化 | システムプロンプトに、生成器の語句（`golden/pdf_golden.py` の語彙の表と `golden/vocab/shift_dev.json` の語句）をそのまま含めない。**これを確かめるテストを追加する**。例示が必要なら、どちらのデータにもない語で書く |
| 3 | ルールの見直し | 合成データのためだけの語に頼らない。否定の表現（「〜不要」「〜ではない」「NO 〜」など）を扱う。解釈できない語は確定させずに要確認に倒す |
| 4 | 回帰 | `pdf_v1` で従来どおり `--gate` 合格。pytest 全件、STEP と DXF の評価も合格のまま |
| 5 | 説明の修正 | ホールドアウトのレポートの「読めた148枚」の表と「値は正しいが要確認」の説明を、`results.csv` で確かめて正しく直す |
| 6 | 最終判定 | 変更後の最終判定は、**新しいホールドアウト `pdf_holdout_s33_v1`（seed=33）で一度だけ**行う（`pdf_holdout_v1` は正解値がレポートに出たため使わない）。条件は元の引き継ぎの受入条件と同じ |
| 7 | 判定用の語彙ずらし | 触れない（リポジトリにない）。完了後に、ユーザー側で安全性の条件で評価する |

## コマンド

```
# 0. 変更前の記録（PR #11 のコードで）
python scripts/generate_pdf_golden.py --per-level 40 --seed 32 --unseen-ratio 0.2 --out pdf_holdout
python scripts/evaluate_pdf.py --data pdf_holdout --reader src.pdf_reader:PdfConditionReader --verify-frozen golden/datasets/pdf_holdout_v1.json --gate --hide-examples --report analysis/pdf_eval_holdout.md
python scripts/generate_pdf_golden.py --per-level 20 --seed 41 --vocab golden/vocab/shift_dev.json --out pdf_shift_dev
python scripts/evaluate_pdf.py --data pdf_shift_dev --reader src.pdf_reader:PdfConditionReader --verify-frozen golden/datasets/pdf_shift_dev_v1.json --gate --gate-profile safety --report analysis/pdf_eval_shift_dev_before.md

# 1〜4. 開発中
python scripts/evaluate_pdf.py --data pdf_shift_dev --reader src.pdf_reader:PdfConditionReader --verify-frozen golden/datasets/pdf_shift_dev_v1.json --gate --gate-profile safety --report analysis/pdf_eval_shift_dev.md
python scripts/evaluate_pdf.py --data pdf_data --reader src.pdf_reader:PdfConditionReader --verify-frozen --gate --report analysis/pdf_eval_latest.md

# 6. 最終判定（最後に一度だけ）
python scripts/generate_pdf_golden.py --per-level 40 --seed 33 --unseen-ratio 0.2 --out pdf_holdout_s33
python scripts/evaluate_pdf.py --data pdf_holdout_s33 --reader src.pdf_reader:PdfConditionReader --verify-frozen golden/datasets/pdf_holdout_s33_v1.json --gate --hide-examples --report analysis/pdf_eval_holdout_s33.md
```

`--hide-examples` は、レポートに図面ごとの正解値を書かない指定。ホールドアウトでは必ず付ける。

## 制約

- 元の引き継ぎの制約はすべて有効（正解データを変えない、金額をLLMに計算させない、pytest からAPIを呼ばない、など）
- **開かない**：`golden/datasets/pdf_holdout_v1.json`、`pdf_holdout_s33_v1.json`、`golden/pdf_unseen.py`。seed=33 のデータは最終判定のときだけ生成する
- **`pdf_shift_dev` の語句をそのままマスターの別名・プロンプト・ルールに足して合格させない**。それでは判定用の語彙ずらしで効かない。一般的な規則（否定の扱い、知らない語は要確認、など）で対応する
- APIの費用：`pdf_v1` を全件キャッシュなしで回すと入力約570万トークン、`pdf_shift_dev` は約190万トークンの見込み。キャッシュ（record モード）と `--limit`・`--kinds` を使って回数を抑える

## 完了時に残すもの

- 改修したコードとテスト（プロンプトに生成器の語句がないことのテストを含む）
- `analysis/pdf_eval_holdout.md`（PR #11 のコードでの完成版）、`analysis/pdf_eval_shift_dev_before.md`、`analysis/pdf_eval_shift_dev.md`、`analysis/pdf_eval_latest.md`、`analysis/pdf_eval_holdout_s33.md`
- `analysis/pdf_llm_comparison.md` に、変更前後の比較（pdf_v1 と pdf_shift_dev の正解率・危険誤答・自動確定・トークン数）を追記
