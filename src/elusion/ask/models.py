"""The pinned model ids of `ask`, in one place.

Every call carries one of these ids and the id the API answers with is stored beside it.
A constant is filled in only after its provider page has been read; the comment beside it
says which page and when. A comment that says NOT CONFIRMED means nobody has read the page
yet, and the value is a placeholder that the owner or the controller must check before the
first paid call.
"""

# Each id below was read on its provider's page on 2026-11-26:
# OpenAI: developers.openai.com/api/docs/models/text-embedding-3-small and /guides/embeddings
# Anthropic: platform.claude.com/docs/en/about-claude/pricing
EMBEDDING_MODEL = "text-embedding-3-small"  # confirmed
# The length of one embedding; the `vector` column is made from it, so a model with another
# length needs a new column (and a new table or a rebuild), not a changed constant.
EMBEDDING_DIM = 1536  # confirmed: the model's default length
ANSWER_MODEL = "claude-sonnet-5-5"  # confirmed
JUDGE_MODEL = "claude-opus-5-5"  # confirmed
DRAFT_MODEL = "claude-opus-5-5"  # confirmed
# What the spend guards of `ask-draft` and `ask-eval` assume one call outputs when its answer
# is short (a question, a verdict). Thinking cannot be turned off on the answer, judge and draft
# models and is billed as output, so the figure is far above the answer itself on purpose.
THINKING_OUTPUT_TOKENS = 1000
