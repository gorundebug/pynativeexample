# Python native example

Direct `aiohttp` + `grpc.aio` implementation of the example business path,
without ServiceLib. Items within one order are sent to Inventory Service
sequentially. HTTP/gRPC payloads, stock rules, error responses and deadlines
match the generated Python example.

```sh
docker compose up --build
```
