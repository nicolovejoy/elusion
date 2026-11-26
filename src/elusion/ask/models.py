"""The pinned model ids of `ask`, in one place.

Every call carries one of these ids and the id the API answers with is stored beside it.
A constant is filled in only after its provider page has been read; the comment beside it
says which page and when. A comment that says NOT CONFIRMED means nobody has read the page
yet, and the value is a placeholder that the owner or the controller must check before the
first paid call.
"""

EMBEDDING_MODEL = "text-embedding-3-small"  # confirmed from developers.openai.com/api/docs/models/text-embedding-3-small on 2026-11-26
# The length of one embedding; the `vector` column is made from it, so a model with another
# length needs a new column (and a new table or a rebuild), not a changed constant.
EMBEDDING_DIM = 1536  # confirmed from developers.openai.com/api/docs/guides/embeddings on 2026-11-26
ANSWER_MODEL = "claude-sonnet-5-5"  # confirmed from platform.claude.com/docs/en/about-claude/pricing on 2026-11-26
JUDGE_MODEL = "claude-opus-5-5"  # confirmed from platform.claude.com/docs/en/about-claude/pricing on 2026-11-26
DRAFT_MODEL = "claude-opus-5-5"  # confirmed from platform.claude.com/docs/en/about-claude/pricing on 2026-11-26
