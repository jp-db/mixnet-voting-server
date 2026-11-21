"""The ballot box: the main server of the mixnet voting system.

Responsibilities: user authentication (password, CAS, OIDC), election
creation and its lifecycle, voter registration, ballot casting, the public
bulletin board, and coordinating the mixnet nodes during counting. Long
work (counting, mixing, verification) runs in Celery workers
(:mod:`ballot_box.worker`), never in the web process.
"""
