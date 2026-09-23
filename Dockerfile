FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/data

WORKDIR /build

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --home-dir /home/app --create-home app

COPY pyproject.toml README.md ./
COPY rss_to_kindle ./rss_to_kindle
COPY scripts ./scripts
COPY tests ./tests

RUN python -m pip install --no-cache-dir .
RUN cd / && python -c "from importlib.resources import files; package = files('rss_to_kindle'); required = ('web_templates/index.html', 'web_static/app.js', 'web_static/style.css'); missing = [path for path in required if not (package / path).is_file()]; assert not missing, f'wheel is missing web assets: {missing}'"

ARG RUN_TESTS=0
RUN if [ "$RUN_TESTS" = "1" ]; then \
      apt-get update \
      && apt-get install --no-install-recommends -y git \
      && python -m unittest discover -s tests -v \
      && apt-get purge --auto-remove -y git \
      && rm -rf /var/lib/apt/lists/*; \
    fi
RUN rm -rf /build

WORKDIR /app

USER 1000:1000

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--worker-class", "sync", "--timeout", "210", "--graceful-timeout", "210", "--error-logfile", "-", "rss_to_kindle.web:create_app()"]
