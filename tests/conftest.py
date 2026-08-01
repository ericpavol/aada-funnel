import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(HERE)
HANDOFF = os.path.abspath(os.path.join(APP_ROOT, "..", "aada-funnel-app-handoff"))
SAMPLE = os.path.join(HANDOFF, "sample_data")

FT_FILE = os.path.join(SAMPLE, "Ping Data - 2 Year 20260706-112027.xlsx")
SUMMER_FILE = os.path.join(SAMPLE, "Ping Data - Summer 20260706-111924.xlsx")
CANONICAL_ENGINE = os.path.join(HANDOFF, "reference", "analysis_engine.py")
