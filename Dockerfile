FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 RF_MODE=hosted RF_LINK_DATA_DIR=/var/lib/rflink/accounts PORT=10000 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
COPY requirements-portal.txt ./
RUN pip install --no-cache-dir -r requirements-portal.txt
COPY pyproject.toml LICENSE README.md ./
COPY src ./src
COPY container_entrypoint.py ./
RUN pip install --no-cache-dir --no-deps . && useradd --create-home --uid 10001 rflink && mkdir -p /var/lib/rflink && chown rflink:rflink /var/lib/rflink
EXPOSE 10000
CMD ["python", "container_entrypoint.py"]
