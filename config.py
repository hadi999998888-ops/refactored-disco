"""Load optional Bothost-style KEY=VALUE settings from Token.env without dependencies.
Environment variables already set by Bothost always take precedence.
"""
import os
from pathlib import Path
p=Path(__file__).resolve().parent/'Token.env'
if p.exists():
    for raw in p.read_text(encoding='utf-8-sig').splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key,value=line.split('=',1)
        key=key.strip(); value=value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key,value)
