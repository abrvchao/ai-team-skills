# Chinese Candidate Discovery V1 regression

`aihot_chinese_v1.json` contains ten live AIHOT observations frozen on 2026-09-30, their pre-change candidate output, and three re-fetched observations whose exact IDs match the problem candidates in historical run `639db207cf1f49290b213861`.

The original run did not persist its ten seed events. The fresh ten are **not** claimed to be that historical batch. The three recovered IDs are verified against the old run output, but their text is the API response at re-fetch time. Source URLs, retrieval timestamps and provenance are included; source claims are not independently verified.

Run offline regression from the Radar directory:

```sh
python3 -m unittest test_chinese_discovery test_discovery test_aihot
```

V1 extracts contiguous observed spans using Chinese punctuation and common phrase boundaries. It suppresses incomplete action/quantity fragments, favors headline spans over summary boilerplate, preserves model versions and explicit GSC queries, and recognizes AI/agent Chinese scope aliases. English-only extraction and Opportunity Score are unchanged. It uses no LLM, external segmenter or new dependencies.

This is a conservative first pass, not general Chinese entity recognition. It may miss short names and unusual sentence structures. The tests check useful-topic retention, source traceability, determinism, known failures and unchanged explicit-query behavior. These checks are not human precision@5 labels or proof of product usefulness.
