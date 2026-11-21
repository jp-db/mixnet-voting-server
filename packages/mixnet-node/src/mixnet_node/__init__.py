"""A mixnet node: one of the k mixing servers of an election.

Each node is meant to be run by a different organisation. It generates and
keeps its own secret key (never sent to the ballot box), and for each
election it:

1. ``POST /elections/{id}/keygen``: generates a key pair, stores it
   encrypted at rest, returns the public key;
2. ``POST /elections/{id}/mix``: takes its input batch (and, for j > 0,
   server j-1's mix record), runs :func:`evmlr.mix`, and returns the output
   batch and its own mix record.

It never sees voter identities beyond the numeric aliases in the first
batch. Requests from the ballot box are authenticated (mTLS or signed
requests, see :mod:`voting_common`).
"""
