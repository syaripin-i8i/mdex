---
type: spec
project: mdex
status: active
updated: 2026-04-09
---

# mdex 記録規約

AI エージェントが残す Markdown ファイルが mdex によって正しく索引化されるための規約です。  
この規約に沿って書かれたファイルは、他のエージェントが `mdex context` で正確に発見できます。

規約はすべて「推奨」であり、欠けていても mdex は動作します。  
ただし規約に沿うほど `context` の精度が上がります。

---

## 1. frontmatter

ファイル先頭に YAML frontmatter を書いてください。

```yaml
---
type: decision        # ノード種別（後述）
project: yura         # プロジェクト名
status: active        # 状態（後述）
updated: 2026-04-09   # 最終更新日（ISO 8601）
tags: [emotion, arch] # 任意。検索・関連付けに使われる
depends_on:           # このファイルを読む前提となるファイル
  - design.md
relates_to:           # 関連するが前提ではないファイル
  - proposal.md
---
```

**最低限書くべきフィールド**: `type` / `status` / `updated`  
この3つがあれば `list` / `find` / `context` が正しく機能します。

---

## 2. type（ノード種別）

| type | 使う場面 |
|------|---------|
| `decision` | アーキテクチャや方針の決定記録。なぜそう決めたかを書く |
| `design` | 設計書・仕様書。実装前に書く |
| `spec` | インターフェース仕様・スキーマ定義 |
| `task` | 作業記録。何をやったか・結果・残課題を書く |
| `log` | セッションログ・作業メモ。時系列で追記される |
| `reference` | 外部仕様の引用・まとめ。変更が少ないもの |

`type` が未指定の場合、ディレクトリ名から推定されます（`decisions/` → `decision`）。  
それも判定できない場合は `unknown` になります。`unknown` は `context` での優先度が下がります。

---

## 3. status（状態）

| status | 意味 |
|--------|------|
| `active` | 現在有効。参照・更新の対象 |
| `draft` | 作成中。まだ確定していない |
| `done` | 完了・解決済み。履歴として残す |
| `archived` | 無効化。参照不要だが削除しない |

`done/` ディレクトリに置かれたファイルは `status` が未指定でも自動的に `done` と判定されます。  
`pending/` も同様に `pending` と判定されます。

---

## 4. ディレクトリ構造（推奨）

明確な構造がないと `type` 推定が `unknown` になりやすいです。

```
project/
  decisions/   ← type: decision
  design/      ← type: design
  specs/       ← type: spec
  tasks/
    pending/   ← status: pending
    done/      ← status: done
  logs/        ← type: log
```

規模が小さい場合はフラットでも構いません。その場合は frontmatter の `type` を必ず書いてください。

---

## 5. summary（要約）

mdex は frontmatter の直後にある最初の段落を summary として使います。  
ここに「このファイルが何について書かれているか」を1〜2文で書いてください。

**良い例**:
```markdown
---
type: decision
...
---

感情モデルのアーキテクチャを state machine から score-based に変更した決定記録。
変更理由・却下した代替案・影響範囲を含む。
```

**悪い例**（summary が死ぬパターン）:
```markdown
---
type: decision
...
---

**Status**: active
**Project**: yura

## 背景
...
```

この場合 summary は `"Status: active"` になります。  
`**Key**: Value` 形式のメタデータをファイル冒頭に置く場合は、その後に空行 + 説明段落を入れてください。

---

## 6. リンク記法

他のファイルへの参照は以下の形式で書いてください。mdex がエッジとして認識します。

```markdown
[[design]]              ← wikilink（拡張子なしでも可）
[設計書](design.md)     ← Markdown リンク
`<repo-root>/design.md`  ← 絶対パス（コードブロック内）
T20260409043442         ← タスク ID（自動で relates_to エッジになる）
```

**`depends_on`**: 前提関係（読む順序がある）は frontmatter に書いてください。  
**`relates_to`**: 横のつながりは frontmatter か本文リンクで。

---

## 7. 最小構成のテンプレート

### decision（決定記録）

```markdown
---
type: decision
project: <project>
status: active
updated: <today>
---

<1〜2文でこの決定が何についてかを書く>

## 決定内容

## 理由

## 却下した代替案

## 影響範囲
```

### task（作業記録）

```markdown
---
type: task
project: <project>
status: done
updated: <today>
relates_to:
  - <関連する設計ファイル>
---

<1〜2文でこのタスクが何をしたかを書く>

## 実施内容

## 結果

## 残課題
```

### log（セッションログ）

```markdown
---
type: log
project: <project>
status: active
updated: <today>
---

<セッションの概要を1文で>

## <日付>

<作業内容・判断・気づき>
```

---

## 8. エージェントへの指示

タスクを完了したら以下を実行してください：

```bash
# 1. 作業記録を残す（上記テンプレートを使う）

# 2. 参照した・更新したファイルの summary を改善する
mdex enrich <node-id> --db <db> --summary "<2〜3文の要約>"
mdex enrich --path <絶対パス> --db <db> --summary-file <要約テキストファイル>

# 3. 索引を更新する
mdex scan --root <dir> --db <db> --config control/scan_config.json
```

この3ステップで次のエージェントが正確にコンテクストを引き出せるようになります。
