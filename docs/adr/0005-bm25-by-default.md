# 5. BM25 for document search by default

**Context.** Eight short rule documents in French, queried with their own vocabulary.
**Decision.** BM25 over passages, no model to load; the E5 embeddings of the earlier RAG project remain an option (`CAMPUS_DOCS_RETRIEVER=dense`).
**Alternatives.** Dense retrieval by default: 1 GB of model on a 512 MB instance, for a corpus where lexical search already finds the passage.
**Consequences.** Paraphrases far from the documents' wording may miss; the grounding suite includes such questions.
