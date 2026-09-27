# Configuration file for the Sphinx documentation builder.
project = "MosTrans Predict"
copyright = "2026, MosTrans Hackathon"
author = "MosTrans Predict Team"
extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon", "sphinx.ext.viewcode"]
templates_path = ["_templates"]
exclude_patterns = ["_build", "sphinx", "**.md", "**.html"]
html_theme = "alabaster"
autodoc_mock_imports = [
    "fastapi",
    "pydantic",
    "pydantic_settings",
    "httpx",
    "catboost",
    "torch",
    "onnxruntime",
    "numpy",
    "pandas",
    "sklearn",
    "starlette",
]
import os
import sys

sys.path.insert(0, os.path.abspath("../backend"))
sys.path.insert(0, os.path.abspath("../ml"))
