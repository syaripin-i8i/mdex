---
type: design
project: mdex
status: active
updated: 2026-07-10
---

# Markdown知識探索基盤 開発提案

## 背景

現在のAI活用では、プロンプトを工夫すること自体よりも、**必要な情報へどう到達させるか** のほうが重要になっている。

## 基本コンセプト

1. Markdown は人間とAIの共通中間表現
2. AIは全文を読むのではなく、まず索引を読む
3. 重要なのは検索ではなく探索（順序制御）

## フェーズ計画

- Phase 1（完了）: Markdownパース + ノード/エッジJSON生成 + 簡易CLI
- Phase 2（完了）: タスク抽出・逆参照・孤立検出
- Phase 3（実装・評価中）: 要約override・探索順序制御・discovery・multi-index
- UI / 可視化（非スコープ）: mdex 本体には Web UI や graph viewer を持たせない。必要な場合は構造化出力を利用する別プロジェクトとして扱う

## 次に見る

- [[AGENT]]
