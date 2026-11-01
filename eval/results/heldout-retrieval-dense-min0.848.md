## heldout-retrieval-dense-min0.848 (23 questions, top-8, embeddings `intfloat/multilingual-e5-base`, reranker `none`)

| Segment | Questions | Hit@1 | Hit@3 | Hit@8 | MRR@8 |
|---|---:|---:|---:|---:|---:|
| All answerable | 12 | 33.3 % | 58.3 % | 83.3 % | 0.487 |
| Language: en | 1 | 100.0 % | 100.0 % | 100.0 % | 1.000 |
| Language: fr | 11 | 27.3 % | 54.5 % | 81.8 % | 0.440 |
| Source: web | 8 | 37.5 % | 62.5 % | 87.5 % | 0.518 |
| Source: xlsx_course_sheet | 4 | 25.0 % | 50.0 % | 75.0 % | 0.425 |

Best dense score, median: answerable 0.864, out of scope 0.812. The current threshold (0.848) refuses 100.0 % of out-of-scope questions and keeps 75.0 % of answerable ones.
Indicative best threshold on this set: 0.846 (balanced accuracy 91.7 %).
