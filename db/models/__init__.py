# Import all models so Alembic autogenerate can discover them.
from db.models.base import Base, TimestampMixin
from db.models.ibge import IbgeState, IbgeMunicipality, IbgeMunicipalityBoundary
from db.models.station import Station, Connector
from db.models.snapshot import StatusSnapshot
from db.models.senatran import SenatranFleet
from db.models.aneel import AneelDistributor, AneelTariff, AneelOutage
from db.models.anp import AnpGasStation, AnpFuelPrice
from db.models.ipea import IpeaSeries, IpeaValue
from db.models.osm import OsmPoi, StationPoiDistance

__all__ = [
    "Base",
    "TimestampMixin",
    "IbgeState",
    "IbgeMunicipality",
    "IbgeMunicipalityBoundary",
    "Station",
    "Connector",
    "StatusSnapshot",
    "SenatranFleet",
    "AneelDistributor",
    "AneelTariff",
    "AneelOutage",
    "AnpGasStation",
    "AnpFuelPrice",
    "IpeaSeries",
    "IpeaValue",
    "OsmPoi",
    "StationPoiDistance",
]
