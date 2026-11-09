# Retrieval evaluation

Golden set: 30 queries, k=10, base `http://localhost:8090`.

| kind | mode | n | hit@1 | recall@k | MRR | mean ms | empty |
|---|---|---|---|---|---|---|---|
| para_easy | keyword | 10 | 0.30 | 0.600 | 0.420 | 126.2 | 0 |
| para_easy | vector | 10 | 0.70 | 0.900 | 0.800 | 53.4 | 0 |
| para_hard | keyword | 10 | 0.10 | 0.300 | 0.124 | 126.2 | 0 |
| para_hard | vector | 10 | 0.10 | 0.200 | 0.114 | 51.4 | 0 |
| para_med | keyword | 10 | 0.40 | 0.500 | 0.450 | 152.8 | 0 |
| para_med | vector | 10 | 0.30 | 0.400 | 0.350 | 53.9 | 0 |

## Misses (recall 0)

| kind | mode | query | expected | got |
|---|---|---|---|---|
| para_med | vector | estimating which sources to trust when several language mode | 2611.04805 | 2610.32144, 2610.33237, 2610.30504 |
| para_med | keyword | estimating which sources to trust when several language mode | 2611.04805 | 2611.08855, 2406.07243, 2610.36915 |
| para_hard | vector | how to tell which helper is reliable when several copies of  | 2611.04805 | 2610.32110, 2611.04090, 2611.02721 |
| para_easy | vector | why prediction intervals lose their guarantee when people re | 2611.05277 | 2602.24207, 2604.03387, 2611.06158 |
| para_easy | keyword | why prediction intervals lose their guarantee when people re | 2611.05277 | 2610.37361, 2610.36126, 2512.14221 |
| para_med | vector | uncertainty intervals become invalid once the model's output | 2611.05277 | 2611.03592, 2605.06612, 2610.37731 |
| para_med | keyword | uncertainty intervals become invalid once the model's output | 2611.05277 | 2610.36126, 2611.06748, 2611.06022 |
| para_hard | vector | prediction bands stop keeping their promise once making a fo | 2611.05277 | 2610.23917, 2611.01098, 2611.07473 |
| para_hard | keyword | prediction bands stop keeping their promise once making a fo | 2611.05277 | 2611.01621, 2610.34797, 2610.29784 |
| para_med | vector | using learned generators to offer several plausible answers  | 2610.35123 | 2610.36751, 2611.06912, 2607.20321 |
| para_hard | keyword | learned models that invent plausible detail where measuremen | 2610.35123 | 2610.31504, 2611.05892, 2610.35464 |
| para_hard | vector | what a chatbot does to itself when allowed to rewrite its ow | 2611.01304 | 2610.34938, 2606.13174, 2611.09376 |
| para_hard | keyword | what a chatbot does to itself when allowed to rewrite its ow | 2611.01304 | 2611.07640, 2611.04225, 2610.31438 |
| para_easy | keyword | whether a small agent should learn from its teacher's demons | 2610.31918 | 2609.32303, 2611.09472, 2611.07130 |
| para_med | keyword | which level of behaviour an agent should imitate: the mentor | 2610.31918 | 2610.36849, 2611.03133, 2611.02074 |
| para_hard | vector | should a compact model learn from its mentor's successes or  | 2610.31918 | 2610.31846, 2510.08564, 2511.07107 |
| para_hard | keyword | should a compact model learn from its mentor's successes or  | 2610.31918 | 2610.31846, 2611.08156, 2610.31842 |
| para_easy | keyword | asking a model for several estimates at once does not consis | 2611.00597 | 2611.04598, 2610.41129, 2604.27239 |
| para_hard | vector | asking a model for several guesses at once does not reliably | 2611.00597 | 2507.16199, 2611.09803, 2610.25768 |
| para_hard | keyword | asking a model for several guesses at once does not reliably | 2611.00597 | 2606.23042, 2610.36915, 2606.19544 |
| para_med | vector | why a screening campaign can get worse as you add more datab | 2611.02571 | 2611.06372, 2610.41970, 2611.07258 |
| para_hard | vector | why piling on more information sources can make a screening  | 2611.02571 | 2610.39339, 2610.38532, 2611.05572 |
| para_med | vector | using clustering to pick where to sample when each evaluatio | 2601.07442 | 2611.09600, 2604.07143, 2610.33885 |
| para_med | keyword | using clustering to pick where to sample when each evaluatio | 2601.07442 | 2610.16876, 2602.10984, 2609.31940 |
| para_hard | vector | speeding up expensive trial-and-error optimisation by groupi | 2601.07442 | 2611.05903, 2611.03179, 2611.09699 |
| para_hard | keyword | speeding up expensive trial-and-error optimisation by groupi | 2601.07442 | 2608.12290, 2611.06819, 2610.31709 |
| para_easy | keyword | a dual characterisation of the regulariser that adversarial  | 2604.18540 | 2610.38851, 2611.02625, 2602.02626 |
| para_med | vector | the mathematical dual of the smoothing penalty implicit in t | 2604.18540 | 2611.03734, 2512.01782, 2611.02037 |
| para_med | keyword | the mathematical dual of the smoothing penalty implicit in t | 2604.18540 | 2512.01782, 2605.14276, 2610.15359 |
| para_hard | vector | the mathematical structure of the smoothing term that appear | 2604.18540 | 2610.36458, 2606.11319, 2611.05224 |
| para_hard | keyword | the mathematical structure of the smoothing term that appear | 2604.18540 | 2610.40934, 2610.31322, 2611.07063 |
