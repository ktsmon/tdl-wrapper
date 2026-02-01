"""Configuration management for TDL wrapper."""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional


class Config:
    """Manages configuration for TDL wrapper."""

    DEFAULT_CONFIG = {
        'tdl_path': 'tdl',
        'database': {
            'path': 'tdl_wrapper.db'
        },
        'downloads': {
            'base_directory': './downloads',
            'organize_by_chat': True,
            'rename_by_timestamp': True,     # Rename files using message timestamps
            'timeout_idle_seconds': 10,      # Kill after X seconds of no log activity
            'timeout_total_seconds': 300     # Absolute maximum timeout (5 minutes)
        },
        'exports': {
            'base_directory': './exports',
            'include_content': True,
            'include_all': False
        },
        'scheduler': {
            'enabled': True,
            'cron_schedule': '0 */6 * * *'  # Global cron schedule: every 6 hours
            # timezone: Use system timezone (set via TZ env var in Docker)
        },
        'discord': {
            'enabled': False,
            'webhook_url': '',
            'notify_on_start': True,
            'notify_on_complete': True,
            'notify_on_error': True,
            'notify_batch_summary': True
        },
        'web': {
            'enabled': True,
            'host': '127.0.0.1',
            'port': 5000,
            'debug': False
        },
        'logging': {
            'level': 'INFO',
            'file': 'tdl_wrapper.log',
            'max_bytes': 10485760,
            'backup_count': 5
        }
    }

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration.

        Args:
            config_path: Path to YAML config file (optional)
        """
        self.config_path = config_path or self._find_config_file()
        self.config = self._load_config()

    def _find_config_file(self) -> Optional[str]:
        """Find config file in standard locations."""
        search_paths = [
            Path.cwd() / 'config.yaml',
            Path.cwd() / 'config.yml',
            Path.home() / '.tdl-wrapper' / 'config.yaml',
            Path.home() / '.config' / 'tdl-wrapper' / 'config.yaml',
        ]

        for path in search_paths:
            if path.exists():
                return str(path)

        return None

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or use defaults."""
        config = self.DEFAULT_CONFIG.copy()

        if self.config_path and Path(self.config_path).exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    user_config = yaml.safe_load(f) or {}
                    config = self._merge_configs(config, user_config)
            except Exception as e:
                print(f"Warning: Error loading config file: {e}")
                print("Using default configuration")

        # Override with environment variables
        config = self._apply_env_overrides(config)

        return config

    def _merge_configs(self, base: Dict, override: Dict) -> Dict:
        """Recursively merge two config dictionaries."""
        result = base.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_configs(result[key], value)
            else:
                result[key] = value

        return result

    def _apply_env_overrides(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply environment variable overrides."""
        # Discord webhook URL
        if 'TDL_DISCORD_WEBHOOK' in os.environ:
            config['discord']['webhook_url'] = os.environ['TDL_DISCORD_WEBHOOK']
            config['discord']['enabled'] = True

        # TDL path
        if 'TDL_PATH' in os.environ:
            config['tdl_path'] = os.environ['TDL_PATH']

        # Database path
        if 'TDL_DB_PATH' in os.environ:
            config['database']['path'] = os.environ['TDL_DB_PATH']

        # TDL data directory (for session storage)
        if 'TDL_DATA_DIR' in os.environ:
            config['tdl_data_dir'] = os.environ['TDL_DATA_DIR']

        # Web server configuration
        if 'WEB_HOST' in os.environ:
            config['web']['host'] = os.environ['WEB_HOST']

        if 'WEB_PORT' in os.environ:
            config['web']['port'] = int(os.environ['WEB_PORT'])

        return config

    def get(self, key: str = None, default: Any = None) -> Any:
        """
        Get configuration value.

        Args:
            key: Dot-separated key (e.g., 'discord.webhook_url')
            default: Default value if key not found

        Returns:
            Configuration value
        """
        if key is None:
            return self.config

        keys = key.split('.')
        value = self.config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default

        return value

    def set(self, key: str, value: Any):
        """
        Set configuration value.

        Args:
            key: Dot-separated key
            value: Value to set
        """
        keys = key.split('.')
        config = self.config

        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        config[keys[-1]] = value

    def save(self, path: Optional[str] = None):
        """
        Save configuration to file.

        Args:
            path: Path to save config (uses config_path if not specified)
        """
        save_path = path or self.config_path

        if not save_path:
            save_path = str(Path.cwd() / 'config.yaml')

        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        with open(save_path, 'w', encoding='utf-8') as f:
            yaml.dump(self.config, f, default_flow_style=False, sort_keys=False)

        print(f"Configuration saved to: {save_path}")

    def __getitem__(self, key: str) -> Any:
        """Get configuration value using dict-like syntax."""
        return self.config[key]

    def __setitem__(self, key: str, value: Any):
        """Set configuration value using dict-like syntax."""
        self.config[key] = value
