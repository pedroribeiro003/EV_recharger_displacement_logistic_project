"""
utils/http_client.py
Cliente HTTP com retry, timeout e logging centralizados.
"""

import time
from typing import Any

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import get_settings
from utils.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


def build_session() -> Session:
    """
    Cria uma requests.Session com retry automático.
    Retries em: 500, 502, 503, 504 e erros de conexão.
    """
    retry_strategy = Retry(
        total=settings.pipeline_retry_attempts,
        backoff_factor=settings.pipeline_retry_delay,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    session: Session | None = None,
) -> Any:
    """
    GET JSON com retry e logging.
    Levanta requests.HTTPError em respostas >= 400.
    """
    _session = session or build_session()
    logger.debug("GET %s params=%s", url, params)

    resp: Response = _session.get(
        url,
        params=params,
        headers=headers,
        timeout=settings.pipeline_request_timeout,
    )
    resp.raise_for_status()
    return resp.json()
