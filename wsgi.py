"""Production WSGI entrypoint, used by gunicorn:  gunicorn wsgi:app"""
from app import create_app

app = create_app()
