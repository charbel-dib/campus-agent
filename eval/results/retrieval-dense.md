## retrieval-dense (60 questions, top-8, embeddings `intfloat/multilingual-e5-base`, reranker `none`)

| Segment | Questions | Hit@1 | Hit@3 | Hit@8 | MRR@8 |
|---|---:|---:|---:|---:|---:|
| All answerable | 48 | 68.8 % | 81.2 % | 93.8 % | 0.770 |
| Language: en | 7 | 71.4 % | 85.7 % | 100.0 % | 0.810 |
| Language: fr | 41 | 68.3 % | 80.5 % | 92.7 % | 0.763 |
| Source: pdf | 3 | 0.0 % | 0.0 % | 33.3 % | 0.056 |
| Source: web | 36 | 75.0 % | 86.1 % | 100.0 % | 0.837 |
| Source: xlsx_course_sheet | 9 | 66.7 % | 88.9 % | 88.9 % | 0.741 |

Best dense score, median: answerable 0.865, out of scope 0.814. The current threshold (0.08) refuses 0.0 % of out-of-scope questions.
Indicative best threshold on this set: 0.848 (balanced accuracy 90.6 %).
