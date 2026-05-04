"""
Clawrity — Client Configuration Loader

Scans config/clients/ for YAML files and parses each into a ClientConfig model.
Supports ${ENV_VAR} interpolation in YAML values.
New client = new YAML file. Zero code changes.
"""

import os
import re
import glob
import logging
from typing import Dict, List, Optional
from pathlib import Path

import yaml
from pydantic import BaseModel

from config.settings import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models for client YAML structure
# ---------------------------------------------------------------------------

class DataSourceConfig(BaseModel):
    type: str = "csv"
    path: str = ""


class DatabaseConfig(BaseModel):
    url: str = ""
    schema_name: str = ""  # 'schema' is a Pydantic reserved attr


class ScoutConfig(BaseModel):
    sector: str = ""
    competitors: List[str] = []
    keywords: List[str] = []
    news_lookback_days: int = 1


class ClientConfig(BaseModel):
    client_id: str
    client_name: str = ""

    data_source: DataSourceConfig = DataSourceConfig()
    database: DatabaseConfig = DatabaseConfig()

    countries: List[str] = []
    risk_threshold: float = 0.15
    hallucination_threshold: float = 0.75

    digest_schedule: str = "08:00"
    timezone: str = "UTC"

    channels: Dict[str, str] = {}

    soul_file: str = ""
    heartbeat_file: str = ""

    column_mapping: Dict[str, str] = {}

    scout: ScoutConfig = ScoutConfig()

    # Runtime: workspace/team ID → client_id mapping for ProtocolAdapter
    slack_workspace_ids: List[str] = []


# ---------------------------------------------------------------------------
# Environment variable interpolation
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _interpolate_env(value: str) -> str:
    """Replace ${ENV_VAR} placeholders with actual environment variable values."""
    def _replace(match):
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    if isinstance(value, str):
        return _ENV_PATTERN.sub(_replace, value)
    return value


def _interpolate_dict(d: dict) -> dict:
    """Recursively interpolate environment variables in a dictionary."""
    result = {}
    for key, value in d.items():
        if isinstance(value, dict):
            result[key] = _interpolate_dict(value)
        elif isinstance(value, list):
            result[key] = [
                _interpolate_env(v) if isinstance(v, str) else v
                for v in value
            ]
        elif isinstance(value, str):
            result[key] = _interpolate_env(value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_client_configs(config_dir: Optional[str] = None) -> Dict[str, ClientConfig]:
    """
    Load all client YAML files from the config directory.

    Returns:
        Dict mapping client_id → ClientConfig
    """
    if config_dir is None:
        config_dir = get_settings().clients_config_dir

    configs: Dict[str, ClientConfig] = {}
    yaml_pattern = os.path.join(config_dir, "*.yaml")

    for yaml_path in glob.glob(yaml_pattern):
        try:
            with open(yaml_path, "r") as f:
                raw = yaml.safe_load(f)

            if not raw or "client_id" not in raw:
                logger.warning(f"Skipping {yaml_path}: missing client_id")
                continue

            # Interpolate environment variables
            interpolated = _interpolate_dict(raw)

            # Handle 'schema' → 'schema_name' mapping for Pydantic
            if "database" in interpolated and "schema" in interpolated["database"]:
                interpolated["database"]["schema_name"] = interpolated["database"].pop("schema")

            config = ClientConfig(**interpolated)
            configs[config.client_id] = config
            logger.info(f"Loaded client config: {config.client_id} from {yaml_path}")

        except Exception as e:
            logger.error(f"Error loading {yaml_path}: {e}")

    if not configs:
        logger.warning(f"No client configs found in {config_dir}")

    return configs


def get_client_config(client_id: str, configs: Optional[Dict[str, ClientConfig]] = None) -> Optional[ClientConfig]:
    """Get a specific client config by ID."""
    if configs is None:
        configs = load_client_configs()
    return configs.get(client_id)
