#!/bin/zsh -l

# Get the directory where this script is stored
# :A resolves symbolic links to the actual physical path
SCRIPT_DIR="${0:A:h}"

# If your pyproject.toml is in the same folder as the script:
pixi run --manifest-path "$SCRIPT_DIR/pyproject.toml" -e default python "$@"