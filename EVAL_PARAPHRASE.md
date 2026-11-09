# Retrieval evaluation

Golden set: 10 queries, k=10, base `http://localhost:8090`.

| kind | mode | n | hit@1 | recall@k | MRR | mean ms | empty |
|---|---|---|---|---|---|---|---|
| paraphrase | keyword | 10 | 0.10 | 0.300 | 0.124 | 145.1 | 0 |
| paraphrase | vector | 10 | 0.10 | 0.100 | 0.100 | 54.4 | 0 |

## Misses (recall 0)

| kind | mode | query | expected | got |
|---|---|---|---|---|
| paraphrase | vector | how to tell which helper is reliable when several copies of  | 2611.04805 | 2610.32110, 2605.17467, 2611.07140 |
| paraphrase | vector | prediction bands stop keeping their promise once making a fo | 2611.05277 | 2611.09815, 2611.06559, 2611.06307 |
| paraphrase | keyword | prediction bands stop keeping their promise once making a fo | 2611.05277 | 2611.01621, 2610.34797, 2610.29784 |
| paraphrase | vector | learned models that invent plausible detail where measuremen | 2610.35123 | 2610.32601, 2610.34471, 2602.14885 |
| paraphrase | keyword | learned models that invent plausible detail where measuremen | 2610.35123 | 2610.31504, 2611.05892, 2610.35464 |
| paraphrase | vector | what a chatbot does to itself when allowed to rewrite its ow | 2611.01304 | 2611.08428, 2611.07218, 2610.39398 |
| paraphrase | keyword | what a chatbot does to itself when allowed to rewrite its ow | 2611.01304 | 2611.07640, 2611.04225, 2610.31438 |
| paraphrase | vector | should a compact model learn from its mentor's successes or  | 2610.31918 | 2611.04565, 2611.05287, 2610.34043 |
| paraphrase | keyword | should a compact model learn from its mentor's successes or  | 2610.31918 | 2610.31846, 2611.08156, 2610.31842 |
| paraphrase | vector | asking a model for several guesses at once does not reliably | 2611.00597 | 2610.35826, 2602.24207, 2610.42000 |
| paraphrase | keyword | asking a model for several guesses at once does not reliably | 2611.00597 | 2606.23042, 2610.36915, 2606.19544 |
| paraphrase | vector | why piling on more information sources can make a screening  | 2611.02571 | 2611.07113, 2610.40890, 2610.30785 |
| paraphrase | vector | speeding up expensive trial-and-error optimisation by groupi | 2601.07442 | 2610.36774, 2610.32179, 2611.04313 |
| paraphrase | keyword | speeding up expensive trial-and-error optimisation by groupi | 2601.07442 | 2608.12290, 2611.06819, 2610.31709 |
| paraphrase | vector | the mathematical structure of the smoothing term that appear | 2604.18540 | 2610.15359, 2610.34934, 2611.02166 |
| paraphrase | keyword | the mathematical structure of the smoothing term that appear | 2604.18540 | 2610.40934, 2610.31322, 2611.07063 |
