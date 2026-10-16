FROM python:3.14.8-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/robocairn

WORKDIR /opt/robocairn
COPY robocairn.py robocairn_app.py fixtures.json oracle.json README.md pyproject.toml LICENSE NOTICE ./

USER 65532:65532
EXPOSE 8765
ENTRYPOINT ["python", "-m", "robocairn_app"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8765"]
