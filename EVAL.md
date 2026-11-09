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
