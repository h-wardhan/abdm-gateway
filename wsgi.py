"""Production entry point: gunicorn wsgi:app"""
from src.app import create_app

app = create_app("production")
