# launch-check

日本語のWebサイト・ツールを公開する前に流す、Claude Code のプラグインです。

1. **機械チェック**（Python 3 の標準ライブラリだけ）: 生成AIのテンプレによく出る見た目と言い回し、広告表示（ステマ規制）・断定表現・免責、APIキーなどの混入、タイトルなどの基本をビルド済みのHTMLから探し、ファイル名・行番号・直し方を出します。
2. **レビュー担当エージェント3つ**: デザイン（PCとスマホの実画面を撮る）、SEO、QA（計算と数字を一次資料と照合）。

AIで作ったサイトは、同じ決まり文句・絵文字の見出し・紫のグラデーション・PR表記のない広告リンクなどが残ったまま公開されやすく、毎回人が目で探すと見落とします。機械で拾えるものは機械で止め、残りを担当エージェントが見る、という分担です。

## 何を調べるか

FAIL が1件でもあると終了コード 1 になります（`--strict` なら WARN でも 1）。

| ID | 重さ | 内容 | 理由 |
| --- | --- | --- | --- |
| AI01 | FAIL | 見出し（h1〜h6）の絵文字 | テンプレ生成のページに多く、見出しの読みやすさも下がる |
| AI02 | FAIL | 決まり文句（「シームレス」「次のレベル」「を実現します」「いかがでしたか」など約20語） | 具体的な内容がない文になりやすい。語ごとに直し方を出す |
| AI03 | WARN | 日本語ページの英語だけの見出し・ボタン・ラベル（「FEATURES」「How it works」） | 読者の言語と合っていない |
| AI04 | WARN | 「送信しません」系の安心文が1ページに2回以上 | くり返すと定型文に見える |
| AI05 | WARN | 紫〜藍のグラデーション（CSS、`<style>`、Tailwind の `bg-gradient-to-* from-indigo-*` など） | 生成AIのテンプレで多い配色 |
| QA01 | FAIL | 画面に `NaN` `undefined` `Infinity` `[object Object]` `{{ 変数 }}` | 計算・テンプレートの壊れ |
| QA02 | FAIL / WARN | 仮の文言（Lorem ipsum、TODO、ダミーテキスト）/ 伏せ字（〇〇） | 未完成のまま公開している |
| QA03 | WARN | 円の小数（`12,345.67円`）。`31.0円/kWh` のような単価は対象外 | 金額は円単位で見せる |
| LG01 | FAIL / WARN | 広告リンク（主なASP、Amazonの `tag=`、`rel="sponsored"`）があるのに「広告」「PR」などの表記がない / 表記が最初の広告リンクより後ろ | 2023年10月1日から、広告であることが分かりにくい表示は景品表示法の不当表示（ステマ規制） |
| LG02 | WARN | 広告リンクに `rel="sponsored"`（または `nofollow`）がない | Google が広告リンクに付けるよう求めている |
| LG03 | FAIL / WARN | 「No.1」「最安」「日本一」「業界初」「満足度100%」など。近くに「調査」「※」などの注記があれば WARN | 根拠のない最上級表示は景品表示法の優良誤認・有利誤認になりうる |
| LG04 | WARN | 「飲むだけで」「必ず痩せる」「元本保証」「ノーリスク」など | 薬機法・健康増進法・金融商品取引法で問題になりやすい言い方 |
| LG05 | WARN | 入力欄のあるページ（計算ツールなど）に免責・前提の記載がない | 結果が目安であること、使った料率の年度を書く |
| LG06 | WARN | 解析・広告タグがあるのに、プライバシーポリシーへのリンクがサイトのどこにもない | 2023年6月施行の電気通信事業法の外部送信規律の対象になる場合がある |
| LG07 | WARN | 決済リンク・購入ボタンがあるのに「特定商取引法に基づく表記」がない | 自分で代金を受け取る通信販売では表記が必要 |
| LG08 | FAIL / WARN | フォームの送信先が `http://` / 外部の `https://` | 暗号化されていない送信 / 送信先の明示 |
| SC01 | FAIL / WARN | APIキー・トークン・秘密鍵らしき文字列（Anthropic、OpenAI、AWS、GitHub、Slack、Stripe の秘密キー。Google の `AIza…` は WARN） | ビルド成果物に入ったキーは誰でも読める。表示は先頭6文字だけ |
| SC02 | FAIL / WARN | 公開ディレクトリの `.env` `*.pem` `.git` など / `.DS_Store` `*.db` | 置くべきでないファイル |
| SC03 | FAIL | 利用者が指定したブロックリストの語 | 社名・本名・取引先名などの混入。表示は1文字目と文字数だけ |
| BS01〜05 | WARN | `<title>` がない、meta description がない、h1 が1つでない、`<html lang>` がない、検索に出すページの本文が400文字未満 | 公開前の基本。404 と `noindex` のページは本文の検査を省く |

HTML の検査は `<script>` `<style>` の中身を除いた、画面に出る文字が対象です。秘密情報の検査は `.html` `.js` `.json` `.txt` `.xml` `.map` `.css` なども対象です。

（表の「例」に挙げた語そのものは、このプラグインのルール一覧です。README をこの検査にかけると、この表の行だけが AI02・QA01〜03 に当たります。）

## インストール

GitHub で公開したあとは、Claude Code で次を実行します。

```bash
claude plugin marketplace add cozy-666/launch-check
claude plugin install launch-check@launch-check
```

公開前に手元で試すときは、このディレクトリを直接読み込みます（設定には何も書き込まれません）。

```bash
claude --plugin-dir ./launch-check
```

必要なもの: Python 3（3.11 と 3.14 でテスト済み。追加のパッケージは不要）。デザイン担当がスクリーンショットを撮るときだけ Node.js（`npx` で Playwright を使う）。

## 使い方

Claude Code の中で:

```text
/launch-check:check out/
/launch-check:check https://example.com/ https://example.com/tool/
/launch-check:check dist/ --blocklist ~/private/blocklist.txt
/launch-check:check out/ --machine-only
```

機械チェックを流して FAIL をソース側で直し、もう一度流してから、`launch-check:designer` `launch-check:seo-reviewer` `launch-check:qa-reviewer` を並行して呼び、まとめて報告します。`--machine-only` ならレビュー担当は呼びません。エージェントは単独でも呼べます（例: `@agent-launch-check:qa-reviewer`）。

スクリプトだけを直接使うこともできます（CI など）。

```bash
python3 scripts/launch_check.py out/
python3 scripts/launch_check.py --urls urls.txt --strict
python3 scripts/launch_check.py out/ --only legal,secret --json
```

| オプション | 内容 |
| --- | --- |
| `--urls ファイル` | URL を1行に1つ書いたファイル。`#` で始まる行は無視 |
| `--only ai,qa,legal,secret,basic` | 検査する分類を絞る |
| `--ignore AI03` | ルールを無視する（くり返し指定可） |
| `--blocklist ファイル` | 公開物に出てはいけない語を1行1語で書いたファイル |
| `--affiliate-host ドメイン` | 広告リンクとして扱うドメインを追加 |
| `--min-text 400` | BS05 の基準文字数 |
| `--strict` | WARN も不合格にする |
| `--json` | 結果を JSON で出す |
| `--max-per-rule 20` | 同じルールの表示件数の上限（0 で全件） |

終了コードは 0 = 通過、1 = FAIL あり、2 = 指定の誤りです。

URL を指定したときは、そのページと、同じホストの CSS（1ページ5件まで）と JavaScript（10件まで）を取りに行きます。ほかのページはたどりません。

### ブロックリスト

プラグインにはブロックリストを入れていません。社名・本名・取引先名など、自分のサイトに出てはいけない語は人ごとに違い、しかもその一覧自体が秘密だからです。リポジトリの外にファイルを置き、`--blocklist` で指定してください。

```text
# 1行1語。大文字・小文字は区別しない
株式会社サンプル
sample-corp.example
```

## 実行例

公開中の静的サイト（Next.js の静的書き出し、HTML 134 ページのうち英語 64 ページ、CSS 4 ファイル、検査したテキストファイル 304 件）に流した結果です。広告リンク 78 件（50 ページ）と解析タグ（132 ページ）がありますが、どれも PR 表記・`rel="sponsored"`・プライバシーポリシーへのリンクがそろっていたため、LG01・LG02・LG06 は出ていません。

```text
$ python3 scripts/launch_check.py <サイトの out ディレクトリ>
（ブロックリスト未指定: 社名・本名などの混入は検査していない。--blocklist で指定できる）
WARN [BS04] 404.html  <html lang> がない
      直し方: <html lang="ja"> を付ける（読み上げ・翻訳・検索が言語を判断する）
WARN [BS04] 404/index.html  <html lang> がない
      直し方: <html lang="ja"> を付ける（読み上げ・翻訳・検索が言語を判断する）

launch-check: 通過（134 ページ / FAIL 0 件 / WARN 2 件）
内訳: BS04 WARN 2
```

所要時間は約1秒でした。この試運転で、次の2つの誤検知を見つけて直しています。

- 品目の絞り込み用の検索欄（`type="search"`）を計算ツールの入力欄と数え、英語ページ 46 件に LG05 を出していた。検索欄を数えないようにし、英語の免責（「official information」など）も認識するようにした
- 本文の「買い替えは購入する店に…」を購入ボタンと見なして LG07 を出していた。リンクとボタンの文字だけを見るようにした

## できないこと

- **AI が書いたかどうかは判定しません。** 決まった語・配色・構造を探すだけです。人が書いた文章でも出ますし、AI が書いても出ないことがあります。
- **法令に適合している保証ではありません。** LG 系は、問題になりやすい表示の手がかりを探すだけです。広告の表記位置、No.1 表示の根拠、特商法の表記が必要かどうかは、内容を見て判断してください。必要なら消費者庁などの一次資料や専門家に確認してください。
- 決まり文句や広告ドメインの一覧は固定です。足りない語があれば、`scripts/launchcheck/rules.py` の一覧に追加できます。
- JavaScript を実行しないので、ブラウザで後から描画される文字は検査できません。静的書き出しかサーバー側で描画した HTML を対象にしてください（デザイン担当のスクリーンショットはブラウザで描画した画面を見ます）。
- 見た目の良し悪し、検索で勝てるか、計算が正しいかは機械チェックでは分かりません。それはレビュー担当エージェントの役割です。

## 開発

```bash
python3 -m unittest discover -s tests -v
claude plugin validate .
```

テストは `tests/fixtures/good`（通過するページ）と `tests/fixtures/bad`（ほぼ全ルールに引っかかるページ）、テスト中に組み立てる一時ディレクトリとローカルの HTTP サーバーを使います。キーらしき文字列はリポジトリに置かず、テスト中に組み立てています。

```text
.claude-plugin/plugin.json       プラグインの定義
.claude-plugin/marketplace.json  このリポジトリをマーケットプレイスとして追加するための定義
skills/check/SKILL.md            /launch-check:check
agents/                          designer / seo-reviewer / qa-reviewer
scripts/launch_check.py          機械チェックの入口
scripts/launchcheck/             HTML の読み取り（page.py）・ルール（rules.py）・CLI（cli.py）
tests/                           unittest とフィクスチャ
```

## ライセンス

MIT
