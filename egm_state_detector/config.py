import yaml
from pathlib import Path
from .models import AppConfig

def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    # 轉換 states list to dict (方便 YAML 書寫)
    # 假設 YAML 裡的 states 還是 list of dict 比較好寫，這裡轉一下
    # 或是直接讓 YAML key 就是 state name
    
    return AppConfig(**data)
