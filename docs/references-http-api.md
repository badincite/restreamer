---
title: references-http-api
---
######[References](../docs/references-index.html) > JSON/HTTP API
# JSON/HTTP API

Small http api for additional webapps

* [GET /v1/states](#get-v1-states)
* [GET /v1/progresses](#get-v1-progresses)

---

#### GET /v1/states

Request:

```sh
GET /v1/states HTTP/1.1
Accept: */*
```

Response:

```sh
HTTP/1.1 200 OK
Content-Type: application/json

{
    "repeat_to_local_nginx": {
        "type": "connected"
    },
    "repeat_to_optional_output": {
        "type": "disconnected"
    }
}
```

#### GET /v1/progresses

Request:

```sh
GET /v1/progresses HTTP/1.1
Accept: */*
```

Response:

```sh
HTTP/1.1 200 OK
Content-Type: application/json

{
    "repeat_to_local_nginx": {
        "frames": 12843,
        "current_fps": 24,
        "current_kbps": 1417.2,
        "target_size": 92653,
        "timemark": "00:08:55.59"
    },
    "repeat_to_optional_output": {
        "frames": 220,
        "current_fps": 37,
        "current_kbps": 1246.2,
        "target_size": 1438,
        "timemark": "00:00:09.45"
    }
}
```

---