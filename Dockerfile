FROM python:3.13-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000
CMD ["sh", "-c", "flask --app wsgi db upgrade && gunicorn -w 4 -b 0.0.0.0:5000 wsgi:app"]
