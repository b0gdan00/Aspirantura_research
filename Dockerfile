FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
# IMPORTANT: Serial access to Arduino must be handled by a single process.
# If you increase workers, each worker becomes a separate process and will fight for the COM/tty device.
CMD ["gunicorn", "asp_experiment.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--timeout", "120"]
