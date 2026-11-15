# Getting Started

## Install

```bash
pip install chronos-logger
```

For development, use the pixi environment:

```bash
pixi install
```

## First run

```python
from chronos import logger, parallel

logger.info("Hello from Chronos")
```

## Development commands

```bash
pixi run test-quick   # unit + component, no network
pixi run lint         # ruff check
pixi run fmt          # ruff format
pixi run typecheck    # pyrefly check (strict preset)
pixi run docs         # Sphinx build
pixi run build        # wheel
```
