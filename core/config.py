from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/ev_demand"

    # Tupi
    tupi_base_url: str = "https://api.tupinambaenergia.com.br"
    tupi_origin: str = "https://tupimob.com"
    poll_interval: int = 60

    # ANP
    anp_base_url: str = "https://www.anp.gov.br/api"

    # IPEA
    ipea_base_url: str = "http://www.ipeadata.gov.br/api/odata4"

    # OSM
    overpass_url: str = "https://overpass-api.de/api/interpreter"

    # Logging
    log_level: str = "INFO"
    log_file: str = "ev_demand.log"


settings = Settings()
