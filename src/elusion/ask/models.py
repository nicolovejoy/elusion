"""The pinned model ids of `ask`, in one place.

Every call carries one of these ids and the id the API answers with is stored beside it.
A constant is filled in only after its provider page has been read; the comment beside it
says which page and when. A comment that says NOT CONFIRMED means nobody has read the page
yet, and the value is a placeholder that the owner or the controller must check before the
first paid call.
"""

EMBEDDING_MODEL = "text-embedding-3-small"  # NOT CONFIRMED: check the id on OpenAI's models page
# The length of one embedding; the `vector` column is made from it, so a model with another
# length needs a new column (and a new table or a rebuild), not a changed constant.
EMBEDDING_DIM = 1536  # NOT CONFIRMED: check the default length on OpenAI's models page
ANSWER_MODEL = "claude-sonnet-5-5"  # NOT CONFIRMED: check the id on Anthropic's models page
JUDGE_MODEL = "claude-opus-5-5"  # NOT CONFIRMED: the id is the one pricing.py already holds
DRAFT_MODEL = "claude-opus-5-5"  # NOT CONFIRMED: the id is the one pricing.py already holds
