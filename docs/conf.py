# Configuration file for the Sphinx documentation builder.
project = "MosTrans Predict"
copyright = "2026, MosTrans Hackathon"
author = "MosTrans Predict Team"
extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon", "sphinx.ext.viewcode"]
templates_path = ["_templates"]
exclude_patterns = ["_build"]
html_theme = "alabaster"
import os
import sys

sys.path.insert(0, os.path.abspath("../backend"))
sys.path.insert(0, os.path.abspath("../ml"))
