---
type: design
project: mdex
status: active
updated: 2026-04-07
---

# Markdown知識探索基盤 開発提案

## 背景

現在のAI活用では、プロンプトを工夫すること自体よりも、**必要な情報へどう到達させるか** のほうが重要になっている。

## 基本コンセプト

1. Markdown は人間とAIの共通中間表現
2. AIは全文を読むのではなく、まず索引を読む
3. 重要なのは検索ではなく探索（順序制御）

## フェーズ計画

- Phase 1: Markdownパース + ノード/エッジJSON生成 + 簡易CLI
- Phase 2: タスク抽出・見出し分割・逆参照・孤立検出
- Phase 3: AI最適化（要約自動生成・探索順序制御）
- Phase 4: 軽量WebUI + ノード可視化

## 次に見る

- [[AGENT]]
