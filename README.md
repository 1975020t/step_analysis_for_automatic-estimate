# STEP板金解析デモ

STEP/STP形式の単一・一定板厚の板金部品から、見積に使う次の5項目を取得する第一版デモです。

- 板厚（mm）
- 展開面積（mm²）
- 切断長（mm、外周と穴周囲の合計）
- 穴数
- 曲げ回数

対象と判定できない形状では数値を返さず、unsupported と理由コードを表示します。

## 起動

    .venv\Scripts\python.exe -m streamlit run app.py

ブラウザで .step または .stp を選び、「解析を実行」を押します。成功時は5項目が同じ画面に表示され、「解析根拠と処理段階」から板厚・曲げ・穴・展開の判定根拠を確認できます。失敗時は理由コードと失敗した処理段階を確認できます。

依存関係を新規に用意する場合:

    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt

## 解析方法

src/sheetmetal_analyzer.py が CadQuery/OpenCASCADE でSTEPをSolidとして読み込み、以下を行います。

1. Solidが1個だけであることを確認
2. 平行な対向平面の距離と面積支持から板厚候補を選択
3. 平面および同軸円筒の表裏面を同じ板厚で対応付け
4. 体積÷板厚と表裏面中央の中立面積が整合することを確認
5. 同軸円筒面の組を軸位置ごとに統合して独立曲げ数を取得
6. 中立面を面積保存で展開した計量表現を作成
7. 表裏面以外の切断面積を板厚で割り、展開後の外周＋内周長を取得
8. 切断面の接続成分のうち外周から独立した閉じた成分を穴として数える

この展開表現は見積値を得るための中立面・境界の計量表現です。製造機に渡す2D CAD輪郭ではありません。

## 結果

成功時の主要フィールドは status、thickness_mm、blank_area_mm2、cut_length_mm、hole_count、bend_count です。

解析不可・エラー時は5項目を null のままにし、reason_code、message、stages を返します。

| 理由コード | 意味 |
|---|---|
| INVALID_FILE_TYPE | STEP/STP以外 |
| STEP_READ_ERROR | STEPを読み込めない |
| NO_SOLID | 対象Solidがない |
| MULTIPLE_SOLIDS | 複数Solid |
| NON_CONSTANT_THICKNESS | 一定板厚を確認できない |
| NOT_SHEET_METAL | 板金らしい寸法比・形状でない |
| BEND_UNDETERMINED | 曲げを十分な確度で判定できない |
| FLAT_PATTERN_FAILED | 展開境界を合理的に求められない |
| ANALYZER_UNAVAILABLE | 解析依存ライブラリがない |
| ANALYSIS_ERROR | その他の予期しない解析エラー |

## テスト

    .venv\Scripts\python.exe -m pytest -q

板金解析テストでは、STEPとして書き出した次の形状を再読込して期待値と比較します。

- 丸穴・角穴を持つ平板: 板厚、展開面積、切断長、穴数、曲げ0回
- 90度の単一R曲げ: 板厚、展開面積、切断長、穴0個、曲げ1回
- 複数Solid、立方体、段付き板厚、曲げRのない鋭角折れ、破損STEP、誤拡張子: 数値を返さず理由を区別
- Streamlit UI: STEP入力と実行操作、5項目の同時表示、解析不可理由の表示

既存モジュールの回帰テストも同時に実行されます。

## 第一版の対象と制約

対象は、1個の有効なSolidで、一定板厚の表裏面を平面または同軸円筒として確認できる一般的な直線曲げ板金です。

次は安全のため解析不可にします。

- アセンブリ、複数部品、溶接構造
- 板厚が変化する形状
- 曲げRを持たず、STEP形状だけでは曲げと切削角を区別できない形状
- 対応する内外円筒面を確認できない曲げ
- 絞り、主体が自由曲面の形状、ロール成形など第一版の展開モデル外
- 壊れたSTEP、Solidを持たないSTEP

公差・材質・ベンドアローワンス固有値、加工順序、金型、製造用展開図、見積金額は扱いません。
