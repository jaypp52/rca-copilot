# Claude Code baseline (answer side)

Generated 2026-11-24T11:31:51+00:00 by `python -m eval.claude_code_baseline` · 67 golden questions (60 answerable / 7 unanswerable) · contexts from the real retriever · judge: Claude Code · sonnet with this repo's JUDGE_SYSTEM. **Answers written by Claude through Claude Code sessions with RCA's system prompt, not by the API pipeline** (no token, cost or latency figures).

| Metric | Claude Code · haiku | Claude Code · sonnet | Claude Code · opus |
|---|---|---|---|
| Faithfulness (mean per answer) | 89.6% | 93.4% | 93.5% |
| Faithfulness (all claims pooled) | 88.3% | 92.3% | 93.5% |
| Judged answers / claims | 60 / 206 | 60 / 234 | 60 / 276 |
| Citation coverage (answerable) | 98.9% | 98.1% | 99.1% |
| Abstention accuracy (unanswerable) | 100.0% | 100.0% | 100.0% |
| False-abstention rate (answerable) | 0.0% | 0.0% | 0.0% |
