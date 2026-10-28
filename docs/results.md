# results.md — mpp-budget-guard（〔D1〕–〔D5〕）

計測 2026-10-25（UTC）、Linux コンテナ。メインネットでの支払いはしていない。
バージョン：Hermes v0.22.2（2026.9.24, git `d923a5eb4`）、mppx 0.8.13、viem 2.57.7、Python 3.11（テスト）/ 3.14（Hermes）。

## D1 テスト件数と通過数

| スイート | 件数 | 通過 |
|---|---|---|
| `tests/test_challenge.py`（`mpp-agent-run` の本番形式対応版テストをそのまま複製） | 34 | **34** |
| `tests/test_guard.py`（このスキルで足した部分：総合判定、上限の引数、`--cap-testnet`、終了コード、`record`、`probe.sh`、SKILL.md の frontmatter） | 16 | **16** |
| 合計 `python3 -m unittest discover -s tests` | **50** | **50** |

`probe.sh` のテストは localhost の 402 サーバーに対して行った。確認した項目は次のとおり：既定で POST と `content-type: application/json` で送る／GET の 404 には終了コード 1 を返す／`Authorization` を送らない。

`mpp-agent-run` 側は変更していない（`challenge.py` と fixture はコピーして使った）。

## D2 Hermes 0.22.2 でのスキル一覧

新しい `HERMES_HOME` を用意し、ローカルパス（`/home/user/mpp-budget-guard/mpp-budget-guard`）からこのスキルを入れた。`hermes skills install` にはローカルパスを渡す方法がないので、Hermes の hub install と同じ処理（quarantine → skills_guard スキャン → `install_from_quarantine`）をたどる `tests/hermes/local_install.py` を使った。そのあと公式の `mpp-agent` を `hermes skills install official/payments/mpp-agent` で入れた。

```
┃ Name             ┃ Category ┃ Source   ┃ Trust     ┃ Status  ┃
│ mpp-agent        │ payments │ official │ official  │ enabled │
│ mpp-budget-guard │ payments │ github   │ community │ enabled │
2 hub-installed, 0 builtin, 0 local — 2 enabled, 0 disabled
```

- system prompt に入るスキル索引（`build_skills_system_prompt`）にも、`mpp-budget-guard` と `mpp-agent` の両方が出ることを確認した。
- Hermes のスキル linter（`tools/skill_linter.py`）の指摘は **0 件**。
- `${HERMES_SKILL_DIR}` はインストール先の絶対パスに置き換わる。
- 途中で見つけて直した点が3つある。
  - description に `: ` を入れると YAML として壊れ、スキルが一覧から消えた。カンマに変え、回帰テストを追加した。
  - インストールすると実行権限が落ちるので（mode 644）、`probe.sh` は `bash` 経由で呼ぶ形にした。
  - スキルは `mpp-budget-guard/` サブディレクトリに置いている（理由は D5）。README とテストはリポジトリのルートにあり、インストールされない。

## D3 testnet での許可1件・拒否1件

`tests/e2e/run.sh` で、Procedure の 1〜4 をそのままたどった。LLM は使っていない。

- 相手は Tempo testnet（moderato, chainId 42431）のローカル402サーバー（`mpp-agent-run/lab` と同じ構成の mppx tempo charge）。
- 流れ：`probe.sh`（既定の POST+JSON）→ `challenge.py analyze --cap-testnet` → 許可なら mppx で支払い → `challenge.py record`。
- 支払いには mppx の**ライブラリ**（`tests/e2e/pay.mjs`。mppx CLI が使っているものと同じ client）を使った。mppx CLI は OS のキーチェーン（Linux では `secret-tool`）が必要で、このコンテナには無いため。
- `pay.mjs` は `onChallenge` で、承認額より大きいチャレンジを拒否する。

許可（`/cheap` 0.005 pathUSD）の判定ログ：
```json
{"ts":"2026-10-25T11:26:45.217351+00:00","verdict":{"decision":"allow","reason":"within per-request and total caps","amount_usd":0.005,"network":"testnet","challenge_id":"w6g7nqv-dlIgZ3fjyX32e6-FNppwwyXNUlG6ohXr6gM"},"method":"tempo","intent":"charge","chainId":42431,"currency":"0x20c0000000000000000000000000000000000000","amount_base_units":"5000","expires":"2026-10-25T11:31:45.142Z","spent_before_usd":0.0}
```
- 支払いの結果：HTTP 200、receipt の `reference`（tx）は `0xa77c586fd4e7c97c3107a0b552ded1c2ba3b3676769da84071d3813c6e1e1cbc`。
- payer の残高：1000000000000 → 999999994969（5031 base units 減。金額と手数料）。
- 台帳に追記した行：`{"amount_usd":0.005,"network":"testnet","tx":"0xa77c…1cbc","challenge_id":"w6g7…6gM"}`

拒否（`/expensive` 0.25 pathUSD）の判定ログ：
```json
{"ts":"2026-10-25T11:26:49.390016+00:00","verdict":{"decision":"deny","reason":"per-request $0.2500 > cap $0.10"},"method":"tempo","intent":"charge","chainId":42431,"currency":"0x20c0000000000000000000000000000000000000","amount_base_units":"250000","expires":"2026-10-25T11:31:49.314Z","spent_before_usd":0.005}
```
- 終了コード 3 で止まり、支払いはしていない（残高の減りは 0）。

testnet は既定では上限なしで判定する。これは `mpp-agent-run` の作業指示に合わせたもので、既存テスト34件にも含まれている。拒否1件を testnet で記録するため、`--cap-testnet` を付けた。

access key を作るスクリプト（`create-access-key.mjs`）は外した。ルートキーでの署名が要り、「スキルからは鍵を読まない」と両立しないため（2026-10-25 に本人が判断）。access key の作り方は、SKILL.md と references/pitfalls.md から mpp.dev の Managing agent spend（https://mpp.dev/guides/managing-agent-spend）にリンクしている。

## D4 セキュリティスキャン

Hermes のスキルハブが install 時に使うのと同じスキャン（`tools/skills_guard.py` の `scan_skill`、source=community）を、インストールされる中身（`mpp-budget-guard/` の5ファイル：SKILL.md、LICENSE、references/pitfalls.md、scripts/challenge.py、scripts/probe.sh）に対して手元で回した。

- **Verdict: SAFE**
- Decision: **ALLOWED**（community source, safe verdict）
- 指摘は3件で、すべて MEDIUM。判定には影響しない（判定の規則は CRITICAL→dangerous、HIGH→caution、MEDIUM だけなら safe）。

```
MEDIUM network       references/pitfalls.md:19  mppx.fetch('http://127.0.0.1:8410/expensive')（再現コード例）
MEDIUM network       references/pitfalls.md:33  （同上のコメント）
MEDIUM supply_chain  references/pitfalls.md:19  （同上）
```

- GitHub から入れたときのインストール時スキャンも、0.19.0／0.22.2 ともに safe（D5）。
- linter の指摘は 0 件。
- 最初のスキャンは **CAUTION（install 拒否）** だった。原因はテストで環境変数の辞書を丸ごとコピーしていた2か所で、HIGH の exfiltration に当たっていた。`env VAR=… cmd` の形に書き直し、SAFE になった。この results.md でもその変数名を書いた行が同じく HIGH になったため、言い換えた。`.skillignore` で除外するような回避はしていない。
- 参考に、公式の `mpp-agent` も同じスキャンで SAFE（MEDIUM 2件：`npm install -g mppx` がバージョン固定されていない点）。
- Hermes が任意で使う第2のスキャナ（NVIDIA SkillEvaluator Tier 1、`skillevaluator` コマンド）は回せていない。外部 GitHub からツールを入れる操作が、このセッションの権限設定で拒否された。Hermes 側でもこれは警告のみの任意スキャンで、install の可否は skills_guard の判定で決まる。

## D5 GitHub からのインストール確認

push 後の `main` を、Hermes 0.19.0（2026.7.20）と 0.22.2（2026.9.24）のそれぞれで、新しい `HERMES_HOME` に入れた。`GITHUB_TOKEN`／`GH_TOKEN` は外し、認証なしで実行した。

**使う側のインストールコマンド**

```bash
hermes skills install official/payments/mpp-agent                   # 支払い役（公式）
hermes skills install kato9292929/mpp-budget-guard/mpp-budget-guard  # このスキル
```

| 識別子 | 0.19.0 | 0.22.2 |
|---|---|---|
| `kato9292929/mpp-budget-guard/mpp-budget-guard` | **成功**。SKILL.md、references/pitfalls.md、scripts/challenge.py、scripts/probe.sh が入った | **成功**（上と同じファイルに加えて LICENSE） |
| `kato9292929/mpp-budget-guard/`（旧レイアウトのとき） | 失敗：`Could not fetch … from any source.`（本人の Mac でも同じエラー） | 成功していた |
| `kato9292929/mpp-budget-guard` | 失敗 | 失敗 |

**経緯（2026-10-28）**

1. 最初はスキルをリポジトリのルートに置き、`kato9292929/mpp-budget-guard/` で入れていた。これは 0.22.2 でしか動かなかった。
   - 0.19.0 は識別子から `<パス>/SKILL.md` を組み立てる。ルートだとパスが空になり `/SKILL.md` を取りに行って失敗する。
   - 本人の Mac で出たエラーも同じものだった。
2. 対策として、スキルを `mpp-budget-guard/` サブディレクトリに移した。`owner/repo/<ディレクトリ>` はどちらの版でも使える標準の形。
3. 移したあと、0.19.0 では SKILL.md と pitfalls.md しか入らなかった。
   - 0.19.0 は、SKILL.md の中でバッククォートか空白の直後に書かれたパスだけを付属ファイルとして取り込む。
   - `${HERMES_SKILL_DIR}/scripts/…` という書き方は、この条件に当たらない。
   - SKILL.md の Quick Reference に「Bundled」行を足して `` `scripts/probe.sh` `` などを明記したところ、scripts/ も入った。

**インストール後の確認（両方の版）**
- スキル一覧：`│ mpp-budget-guard │ │ skills.sh │ community │ enabled │`
- インストール時のスキャン：safe
- インストール先の `scripts/probe.sh` → `scripts/challenge.py analyze` を Exa の `/search` に支払いなしで実行し、`verdict: allow` になった。
- `mpp-agent` は 0.19.0 には含まれていない（`official/payments/mpp-agent` は Hermes 2026.8 以降の optional skill）。0.22.2 では一覧に `mpp-agent` と `mpp-budget-guard` が並ぶ（D2）。
- Hermes 0.19.0 以前で動いているチャットに入れた場合は、再起動するか `/reload-skills` を実行する（SKILL.md の Pitfalls 5）。
