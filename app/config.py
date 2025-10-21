from pathlib import Path

import yaml


class Config:
    data: dict

    @classmethod
    def load_config(cls, config_file: Path):
        with config_file.open() as f:
            # use safe_load instead load
            data = yaml.safe_load(f)
        cls.data = data
