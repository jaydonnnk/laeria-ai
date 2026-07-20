"""Stdlib-`re` shim for the `regex` package (Windows SAC workaround).

Windows Smart App Control blocks regex's `_regex` C extension DLL. The only
consumer in this venv is parsimonious (via eth_abi ← eth_account), which
uses nothing beyond stdlib-compatible features: compile(), the I/L/M/S/U/X/A
flags, Pattern.match(text, pos), .pattern, .flags. The stdlib module covers
all of it, so this shim simply re-exports `re`.

Same treatment as infra/bitarray_stub and infra/ckzg_stub: copy over
.venv/Lib/site-packages/regex/ after any venv rebuild. `pip install regex`
would restore the blocked DLL.

If some future dependency needs real `regex`-only features (possessive
quantifiers, \\p{...} properties, fuzzy matching), this shim will fail loudly
at compile() — swap strategies then, don't extend the shim silently.
"""

from re import *  # noqa: F401,F403
from re import (  # noqa: F401 — names `from re import *` doesn't export
    Match,
    Pattern,
    error,
)

# regex-package aliases some consumers reference.
DEFAULT_VERSION = 0
VERSION0 = 0
VERSION1 = 1
