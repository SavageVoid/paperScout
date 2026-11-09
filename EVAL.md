# Retrieval evaluation

Golden set: 180 queries, k=10, base `http://localhost:8090`.

| kind | mode | n | hit@1 | recall@k | MRR | mean ms | empty |
|---|---|---|---|---|---|---|---|
| author | author | 60 | 0.85 | 1.000 | 0.913 | 3.3 | 0 |
| phrase | keyword | 60 | 0.97 | 1.000 | 0.983 | 4.5 | 0 |
| phrase | vector | 60 | 0.37 | 0.617 | 0.455 | 35.2 | 0 |
| title | keyword | 60 | 1.00 | 1.000 | 1.000 | 4.5 | 0 |
| title | vector | 60 | 0.95 | 1.000 | 0.969 | 34.8 | 0 |

## Misses (recall 0)

| kind | mode | query | expected | got |
|---|---|---|---|---|
| phrase | vector | and healthcare more predictive, preventive, personalized, an | 2407.21665 | 2610.24662, 2610.42039, 2605.02740 |
| phrase | vector | the model is instructed to optimize for. In | 2611.01304 | 2611.06879, 2611.04113, 2610.32026 |
| phrase | vector | the current action. We introduce a hierarchical agent | 2610.31918 | 2610.37224, 2611.08905, 2610.37405 |
| phrase | vector | choosing the pool on half the runs and | 2611.02571 | 2611.03117, 2610.39837, 2610.19345 |
| phrase | vector | for the entire domain, employs k-means clustering to | 2601.07442 | 2611.01207, 2610.33531, 2611.09501 |
| phrase | vector | constraint, and existing generators are purely \emph{syntact | 2610.37716 | 2610.35328, 2610.41511, 2610.41089 |
| phrase | vector | temporal and variables structures, and most methods optimise | 2611.07720 | 2611.04063, 2610.26002, 2611.07746 |
| phrase | vector | offer a natural alternative, decomposing the policy into | 2610.31062 | 2611.00536, 2610.37870, 2610.41132 |
| phrase | vector | deterministic. Analytical two-stage examples show persistent | 2611.05225 | 2605.07104, 2609.29117, 2610.37361 |
| phrase | vector | assesses performance without external context. Removing the  | 2611.07988 | 2610.36114, 2606.11806, 2610.34425 |
| phrase | vector | learns the stand-in better. When the design law | 2611.01666 | 2610.40031, 2610.38426, 2611.04565 |
| phrase | vector | instance. Candidate planner prompts are evolved with a | 2610.35426 | 2404.08164, 2611.04320, 2610.39571 |
| phrase | vector | 18.07 to 22.68 percentage points on average with | 2611.03020 | 2608.29843, 2610.38282, 2605.27752 |
| phrase | vector | Our algorithm runs the dynamic program for the | 2611.02739 | 2606.13799, 2602.12300, 2610.35595 |
| phrase | vector | is still lacking} for robust RL, particularly for | 2610.35656 | 2610.32971, 2611.02218, 2605.04373 |
| phrase | vector | selected by minimum redundancy and maximum relevance (mRMR) | 2610.40848 | 2605.25773, 2610.37624, 2610.40966 |
| phrase | vector | foundations, familiarity with graph applications in SCM, and | 2411.08550 | 2606.09484, 2507.22419, 2611.01200 |
| phrase | vector | to logarithmic factors. Under stationarity and mixing condit | 2611.04157 | 2610.31612, 2611.09596, 2611.03305 |
| phrase | vector | score is its probability of beating the best | 2610.36708 | 2610.17256, 2610.30512, 2611.02550 |
| phrase | vector | it plays an important role in making or | 2611.03764 | 2407.18524, 2610.36116, 2610.39869 |
| phrase | vector | premises to research targets, attempted arguments, counterex | 2611.02186 | 2610.39070, 2608.12571, 2611.02234 |
| phrase | vector | across two canonical circuits: the Indirect Object Identific | 2610.37157 | 2609.09124, 2610.11460, 2603.03989 |
| phrase | vector | and biases so that it meets the target | 2611.04074 | 2611.05662, 2611.07270, 2605.05003 |

## How to read this

The table above is the verbatim-vocabulary set: exact titles, eight consecutive words lifted
from an abstract, and full first-author names. Full-text search cannot lose that set, so it is
the floor, not the ceiling. The interesting question is what happens when a query shares
*meaning* with a paper but not words — measured separately, in three tiers over the same ten
papers:

| tier | mode | n | hit@1 | recall@10 | MRR | mean ms |
|---|---|---|---|---|---|---|
| easy — title restated plainly | keyword | 10 | 0.30 | 0.600 | 0.420 | 126 |
| easy — title restated plainly | vector | 10 | **0.70** | **0.900** | **0.783** | 51 |
| med — core claim, no jargon | keyword | 10 | 0.40 | **0.500** | 0.450 | 153 |
| med — core claim, no jargon | vector | 10 | 0.40 | 0.400 | 0.400 | 54 |
| hard — oblique, no shared words | keyword | 10 | 0.10 | **0.300** | 0.124 | 126 |
| hard — oblique, no shared words | vector | 10 | 0.10 | 0.200 | 0.111 | 51 |

Same ten papers in every tier, so a gap between tiers is difficulty, not sampling luck.

## What each mode is good for

**Keyword — exact-match retrieval is solved.** Titles at MRR 1.000 in 4.5 ms; verbatim phrases
at 0.983. Nothing here is worth improving.

**Vector — semantic retrieval works, for answerable queries.** On plain-language descriptions
it beats keyword by **30 points of recall@10** and **36 points of MRR**. That is what the
latency buys. On abstract-level paraphrases it is a coin flip, and on riddles both modes
collapse to 0.2–0.3.

**Author — solved, with a contract that cost a round to discover.** A full first-author name
returns that person's papers (recall@10 1.000, MRR 0.913, 3.3 ms). A bare surname returns
other people entirely. `mode` accepts only `vector` and `keyword`; author mode is inferred
from `author=`.

## How the failures differ, which decides what to build

Measured at `limit=500` of 8,937:

| missed paper | MiniLM rank | bge-small rank | diagnosis |
|---|---|---|---|
| 2611.04805 | 83 | **42** | retrievable but outranked → a **ranking** problem |
| 2611.05277 | absent | **absent** | not represented → a **representation** problem |

A reranker over the candidate pool fixes the first and cannot touch the second; chunking the
abstracts addresses the second at a measured cost of 40 minutes of re-embedding per attempt.

## Two negatives worth keeping

**The model swap moved one query.** Paraphrase recall@10 went 0.100 → 0.200. It did fix a real
defect — MiniLM's 256-token window silently discarded ~33% of every 1,493-character abstract —
and it halved a miss rank (83 → 42). But the headline barely moved, and that was predicted
wrong before it was measured.

**The query instruction changed nothing.** Applied to queries only, confirmed live in
`/health`, with every keyword row byte-identical as the control. bge v1.5 exists partly to
make that instruction unnecessary, and the measurement agreed with the release notes.

## What this does not measure

Relevance beyond one paper per query (precision is unmeasured), queries a real user would
type, anything at all about generation, and any single tier to two decimal places: n=10 gives
a 95% interval of ±0.18. The tier *pattern* is trustworthy; the individual numbers are not.
