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

新しい `HERMES_HOME` を用意し、ローカルパス（`/home/user/mpp-budget-guard`）からこのスキルを入れた。`hermes skills install` にはローカルパスを渡す方法がないので、Hermes の hub install と同じ処理（quarantine → skills_guard スキャン → `install_from_quarantine`）をたどる `tests/hermes/local_install.py` を使った。そのあと公式の `mpp-agent` を `hermes skills install official/payments/mpp-agent` で入れた。

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
  - linter がルートの `README.md` を指摘するので、`docs/README.md` に移した。

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

あわせて `scripts/create-access-key.mjs` も testnet で試した。
- 条件：上限 0.02 pathUSD/日、受取先1件、期限 +1日。
- authorize の tx `0xd186b57374a34dd563d6f904988de8f41689cbdf28e44ccabe1468dca3a995bf` は success。
- access key の秘密鍵は mode 0600 のファイルに保存し、画面には出していない。

## D4 セキュリティスキャン

Hermes のスキルハブが install 時に使うのと同じスキャン（`tools/skills_guard.py` の `scan_skill`、source=community）を、quarantine したバンドル21ファイル（push する内容と同じもの）に対して手元で回した。

- **Verdict: SAFE**
- Decision: **ALLOWED**（community source, safe verdict）
- 指摘 12 件はすべて MEDIUM で、判定には影響しない（判定の規則は CRITICAL→dangerous、HIGH→caution、MEDIUM だけなら safe）。主なものは下のとおり。残り4件は、この results.md が下の一覧を引用している行に対するもの。

```
MEDIUM network       references/pitfalls.md:19  mppx.fetch('http://127.0.0.1:8410/expensive')（再現コード例）
MEDIUM network       references/pitfalls.md:33  （同上のコメント）
MEDIUM supply_chain  references/pitfalls.md:19  （同上）
MEDIUM execution     tests/test_challenge.py:230,239  subprocess.run（CLI テスト）
MEDIUM execution     tests/test_guard.py:56,189       subprocess.run（CLI / probe.sh テスト）
MEDIUM traversal     tests/e2e/run.sh:9               ROOT="$(cd "$E2E/../.." && pwd)"
```

- 最初のスキャンは **CAUTION（install 拒否）** だった。原因はテストで環境変数の辞書を丸ごとコピーしていた2か所で、HIGH の exfiltration に当たっていた。`env VAR=… cmd` の形に書き直し、SAFE になった。この results.md でもその変数名を書いた行が同じく HIGH になったため、言い換えた。`.skillignore` で除外するような回避はしていない。
- 参考に、公式の `mpp-agent` も同じスキャンで SAFE（MEDIUM 2件：`npm install -g mppx` がバージョン固定されていない点）。
- Hermes が任意で使う第2のスキャナ（NVIDIA SkillEvaluator Tier 1、`skillevaluator` コマンド）は回せていない。外部 GitHub からツールを入れる操作が、このセッションの権限設定で拒否された。Hermes 側でもこれは警告のみの任意スキャンで、install の可否は skills_guard の判定で決まる。

## D5 GitHub からのインストール確認

**未実施。** `kato9292929/mpp-budget-guard` が GitHub にまだ無く（このセッションの権限では作成できない）、push していないため。

使う側のインストールコマンド（push 後に、どの形で入るかをここに確定させる）：
- Hermes 0.22.2 の GitHub ソースは、識別子を `owner/repo/<スキルのパス>` として解釈する（`tools/skills_hub_github.py` の `_split_repo_id`）。
- パスが空ならリポジトリのルートをスキルとして扱う。このため `kato9292929/mpp-budget-guard/`（末尾スラッシュ付き）が第1候補になる。
- スラッシュ無しの `kato9292929/mpp-budget-guard` は、GitHub ソースでは区切りが2つしかなく解釈されない。push 後に、両方の形を新しい `HERMES_HOME` で実際に試して結果を書く。
