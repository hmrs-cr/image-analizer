#!/usr/bin/env python3
"""Entrypoint shim -- the actual implementation lives in the image_analyzer package."""
from image_analyzer.app import main

if __name__ == "__main__":
    main()
