# Retrieval evaluation

Golden set: 10 queries, k=10, base `http://localhost:8090`.

| kind | mode | n | hit@1 | recall@k | MRR | mean ms | empty |
|---|---|---|---|---|---|---|---|
| paraphrase | keyword | 10 | 0.10 | 0.300 | 0.124 | 154.6 | 0 |
| paraphrase | vector | 10 | 0.10 | 0.200 | 0.111 | 65.5 | 0 |

## Misses (recall 0)

| kind | mode | query | expected | got |
|---|---|---|---|---|
| paraphrase | vector | how to tell which helper is reliable when several copies of  | 2611.04805 | 2610.36010, 2611.02721, 2611.04090 |
| paraphrase | vector | prediction bands stop keeping their promise once making a fo | 2611.05277 | 2610.23917, 2611.01098, 2510.13385 |
| paraphrase | keyword | prediction bands stop keeping their promise once making a fo | 2611.05277 | 2611.01621, 2610.34797, 2610.29784 |
| paraphrase | keyword | learned models that invent plausible detail where measuremen | 2610.35123 | 2610.31504, 2611.05892, 2610.35464 |
| paraphrase | vector | what a chatbot does to itself when allowed to rewrite its ow | 2611.01304 | 2611.08428, 2606.13174, 2610.33766 |
| paraphrase | keyword | what a chatbot does to itself when allowed to rewrite its ow | 2611.01304 | 2611.07640, 2611.04225, 2610.31438 |
| paraphrase | vector | should a compact model learn from its mentor's successes or  | 2610.31918 | 2611.00452, 2610.31846, 2611.07529 |
| paraphrase | keyword | should a compact model learn from its mentor's successes or  | 2610.31918 | 2610.31846, 2611.08156, 2610.31842 |
| paraphrase | vector | asking a model for several guesses at once does not reliably | 2611.00597 | 2507.16199, 2611.09803, 2610.33237 |
| paraphrase | keyword | asking a model for several guesses at once does not reliably | 2611.00597 | 2606.23042, 2610.36915, 2606.19544 |
| paraphrase | vector | why piling on more information sources can make a screening  | 2611.02571 | 2610.38532, 2610.33962, 2601.22871 |
| paraphrase | vector | speeding up expensive trial-and-error optimisation by groupi | 2601.07442 | 2610.41173, 2611.05903, 2611.03117 |
| paraphrase | keyword | speeding up expensive trial-and-error optimisation by groupi | 2601.07442 | 2608.12290, 2611.06819, 2610.31709 |
| paraphrase | vector | the mathematical structure of the smoothing term that appear | 2604.18540 | 2610.34934, 2610.36458, 2611.08445 |
| paraphrase | keyword | the mathematical structure of the smoothing term that appear | 2604.18540 | 2610.40934, 2610.31322, 2611.07063 |
