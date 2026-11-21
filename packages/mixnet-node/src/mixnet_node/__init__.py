"""A mixnet node: one of the k mixing servers of an election.

Each node is meant to be run by a different organisation. Per election it
generates and keeps its own key pair (:mod:`mixnet_node.keystore`; only the
public key leaves the node), and when counting it decrypts its layer of the
ballots, drops those whose proofs fail, shuffles the rest and proves the
shuffle (:mod:`mixnet_node.jobs`). The HTTP API is in :mod:`mixnet_node.api`.

It never sees voter identities beyond the numeric aliases in the first
batch. Requests from the ballot box carry a bearer token; across
organisations, put mTLS in front as well.
"""
