FROM python:3.12-slim AS build
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --prefix=/install -r requirements.txt grpcio-tools==1.81.0
COPY proto ./proto
COPY src ./src
RUN PYTHONPATH=/install/lib/python3.12/site-packages \
    python -m grpc_tools.protoc -Iproto --python_out=src --grpc_python_out=src \
    proto/processorderitem.proto proto/inventoryserviceapi.proto

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/usr/local/lib/python3.12/site-packages
COPY --from=build /install /usr/local
WORKDIR /app

FROM runtime AS inventoryservice
COPY --from=build /app/src/common.py /app/common.py
COPY --from=build /app/src/inventory_service.py /app/inventory_service.py
COPY --from=build /app/src/*_pb2.py /app/
COPY --from=build /app/src/*_pb2_grpc.py /app/
ENTRYPOINT ["python", "-OO", "inventory_service.py"]

FROM runtime AS orderservice
COPY --from=build /app/src/common.py /app/common.py
COPY --from=build /app/src/order_service.py /app/order_service.py
COPY --from=build /app/src/*_pb2.py /app/
COPY --from=build /app/src/*_pb2_grpc.py /app/
ENTRYPOINT ["python", "-OO", "order_service.py"]
