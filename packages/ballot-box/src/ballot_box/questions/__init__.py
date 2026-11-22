"""Questions: what voters answer, how answers become evmlr votes, and how votes are counted.

evmlr encrypts, mixes and decrypts votes without looking inside them: a vote
is any ``evmlr.VOTE_LEN`` coefficients mod ``evmlr.MODULUS``. This package
gives them meaning, in two versioned, separate steps:

- an **encoding** turns an answer into coefficients (in the booth, mirrored in
  TypeScript) and decodes the coefficients after the count, rejecting
  malformed ones (:mod:`.encodings`);
- a **counting method** turns the valid answers into a result (:mod:`.methods`).

Both names are part of the question's spec, which is bound into the
question's evmlr election id, so a count cannot be reinterpreted later.

Until evmlr proves that a ballot is well formed, nothing stops a voter from
encrypting coefficients no encoding produces. The count discards such votes
and reports them (:func:`tally`); since votes are anonymous by then, anyone
can recheck the list from the published output.
"""

from .encodings import ENCODINGS, Encoding, Malformed
from .methods import METHODS
from .spec import QuestionSpec, crypto_id
from .tally import Discarded, tally

__all__ = [
    "ENCODINGS",
    "METHODS",
    "Discarded",
    "Encoding",
    "Malformed",
    "QuestionSpec",
    "crypto_id",
    "tally",
]
