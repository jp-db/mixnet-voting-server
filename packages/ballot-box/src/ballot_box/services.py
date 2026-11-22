"""What the web app and the workers share: database, blob store, node connections."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from voting_common.blobstore import BlobStore, FilesystemBlobStore

from .config import Settings
from .db import make_engine, make_sessionmaker
from .mixnet import Connector, HttpConnector
from .sealing import Sealer, node_token_sealer


@dataclass
class Services:
    settings: Settings
    db: sessionmaker[Session]
    blobs: BlobStore
    sealer: Sealer
    connect: Connector

    @classmethod
    def from_settings(cls, settings: Settings) -> Services:
        sealer = node_token_sealer(settings.secret_key.get_secret_value())
        return cls(
            settings=settings,
            db=make_sessionmaker(make_engine(settings.database_url)),
            blobs=FilesystemBlobStore(settings.blob_dir),
            sealer=sealer,
            connect=HttpConnector(sealer),
        )
