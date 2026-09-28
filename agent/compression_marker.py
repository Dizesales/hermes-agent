"""The model-visible compression/elision marker and its matcher.

Tool-call arguments are no longer rewritten by the compressor, but legacy sessions may still
carry this marker inside replayed tool-call arguments. Dependency-free leaf shared by the
elision renderers (``agent.context_compressor``) and the dispatch-boundary detector
(``agent.tool_dispatch_helpers``), so the matcher is derived from the template instead of
re-typing its wording.
"""

from __future__ import annotations

import re

# #83714 — this text lands inside the model's OWN replayed tool call, so it must not read like
# something the model would write itself: the bare "...[truncated]" it replaced was imitated into
# new calls and written to disk. Non-prose delimiters, an explicit "not original content"
# disclaimer, and per-instance counts keep a copied marker visibly wrong; the counts also make a
# verbatim copy stale, which is why the marker must never be re-applied.
_COMPRESSION_MARKER_PREFIX = "⟪HERMES-CONTEXT-COMPRESSION:"
_COMPRESSION_MARKER_TEMPLATE = (
    _COMPRESSION_MARKER_PREFIX
    + " {omitted:,} of {total:,} chars omitted here by Hermes's context compressor. "
    "This is NOT part of the original tool call and must never be reproduced in new "
    "output — always write full, untruncated content.⟫"
)

# A minted marker (prefix + rendered counts through the first sentence). The prefix alone
# does not match, so source/docs that mention the constant can still be edited.
_COMPRESSION_MARKER_RE = re.compile(
    re.escape(_COMPRESSION_MARKER_TEMPLATE.split(". ", 1)[0] + ".")
    .replace(re.escape("{omitted:,}"), r"\d[\d,]*")
    .replace(re.escape("{total:,}"), r"\d[\d,]*")
)

