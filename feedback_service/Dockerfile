FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AI_DAILY_FEEDBACK_HOST=0.0.0.0 \
    AI_DAILY_FEEDBACK_PORT=8765 \
    AI_DAILY_FEEDBACK_DB=/data/feedback.db

WORKDIR /app
COPY feedback_service /app/feedback_service

VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:' + __import__('os').environ.get('AI_DAILY_FEEDBACK_PORT', __import__('os').environ.get('PORT', '8765')) + '/healthz', timeout=3).read()"

CMD ["python", "-m", "feedback_service.server"]
