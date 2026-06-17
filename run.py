import requests
print("REQUESTS:", requests.__file__)


#!/usr/bin/env python
"""
AETHER - Entrypoint Principal
Ejecuta: python run.py
"""

import sys
from pathlib import Path

# Asegurar que el directorio raíz esté en sys.path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from cli.main import main

if __name__ == "__main__":
    main()
