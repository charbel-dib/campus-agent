## retrieval-rerank (60 questions, top-8, embeddings `intfloat/multilingual-e5-base`, reranker `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`)

| Segment | Questions | Hit@1 | Hit@3 | Hit@8 | MRR@8 |
|---|---:|---:|---:|---:|---:|
| All answerable | 48 | 70.8 % | 89.6 % | 95.8 % | 0.806 |
| Language: en | 7 | 71.4 % | 100.0 % | 100.0 % | 0.833 |
| Language: fr | 41 | 70.7 % | 87.8 % | 95.1 % | 0.802 |
| Source: pdf | 3 | 0.0 % | 0.0 % | 66.7 % | 0.114 |
| Source: web | 36 | 72.2 % | 97.2 % | 100.0 % | 0.844 |
| Source: xlsx_course_sheet | 9 | 88.9 % | 88.9 % | 88.9 % | 0.889 |

Best dense score, median: answerable 0.865, out of scope 0.814. The current threshold (0.08) refuses 0.0 % of out-of-scope questions.
Indicative best threshold on this set: 0.848 (balanced accuracy 90.6 %).
