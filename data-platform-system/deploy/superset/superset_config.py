import os


SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = os.environ["SUPERSET_SQLALCHEMY_DATABASE_URI"]

WTF_CSRF_ENABLED = True
ENABLE_PROXY_FIX = True
TALISMAN_ENABLED = False

FEATURE_FLAGS = {
    "DASHBOARD_NATIVE_FILTERS": True,
    "ENABLE_TEMPLATE_PROCESSING": True,
}

APP_NAME = "BDSH Superset"
APP_ICON = "/static/assets/images/superset-logo-horiz.png"

LANGUAGES = {
    "zh": {"flag": "cn", "name": "Chinese"},
    "en": {"flag": "us", "name": "English"},
}
