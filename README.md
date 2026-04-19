# ONQL Python Driver

Official Python client for the ONQL database server.

## Installation

### From PyPI

```bash
pip install onql-client
```

### From GitHub (latest `main`)

```bash
pip install git+https://github.com/ONQL/onqlclient-python.git
```

### Pinned to a release tag

```bash
pip install git+https://github.com/ONQL/onqlclient-python.git@v0.1.5
```

## Quick Start

```python
import asyncio
from onqlclient import ONQLClient

async def main():
    client = await ONQLClient.create("localhost", 5656)

    # Execute a query
    result = await client.send_request("onql", json.dumps({
        "db": "mydb",
        "table": "users",
        "query": 'name = "John"'
    }))
    print(result["payload"])

    # Subscribe to live updates
    rid = await client.subscribe("", 'name = "John"', lambda rid, kw, payload:
        print(f"Update: {payload}")
    )

    # Unsubscribe
    await client.unsubscribe(rid)

    await client.close()

asyncio.run(main())
```

## API Reference

### `ONQLClient.create(host, port, data_limit, default_timeout)`

Creates and returns a connected client instance.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `host` | `str` | `"localhost"` | Server hostname |
| `port` | `int` | `5656` | Server port |
| `data_limit` | `int` | `16777216` | Max buffer size in bytes (16 MB) |
| `default_timeout` | `int` | `10` | Default request timeout in seconds |

### `await client.send_request(keyword, payload, timeout=None)`

Sends a request and waits for a response. Returns a dict with `request_id`, `source`, and `payload`.

### `await client.subscribe(onquery, query, callback)`

Opens a streaming subscription. Returns the subscription ID. Callback receives `(rid, keyword, payload)`.

### `await client.unsubscribe(rid)`

Stops receiving events for a subscription.

### `await client.close()`

Closes the connection.

## Direct ORM-style API

On top of the raw `send_request` protocol, the client ships convenience methods
that build the standard payload envelopes for common operations and unwrap
the `{error, data}` response automatically.

Call `client.setup(db)` once to bind a default database name; every subsequent
`insert` / `update` / `delete` / `onql` call will use it.

### `client.setup(db)`

Sets the default database. Returns `self`, so calls can be chained.

```python
client.setup("mydb")
```

### `await client.insert(table, data)`

Insert one record or a list of records.

| Parameter | Type | Description |
|-----------|------|-------------|
| `table` | `str` | Target table name |
| `data` | `dict \| list[dict]` | A single record or a list of records |

Returns the parsed `data` field from the server response. Raises `Exception`
when the server returns a non-empty `error` field.

```python
await client.insert("users", {"name": "John", "age": 30})
await client.insert("users", [{"name": "A"}, {"name": "B"}])
```

### `await client.update(table, data, query, protopass="default", ids=None)`

Update records matching `query`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `table` | `str` | — | Target table |
| `data` | `dict` | — | Fields to update |
| `query` | `dict \| str` | — | Match query |
| `protopass` | `str` | `"default"` | Proto-pass profile |
| `ids` | `list[str]` | `[]` | Explicit record IDs |

```python
await client.update("users", {"age": 31}, {"name": "John"})
await client.update("users", {"active": False}, {"id": "u1"}, protopass="admin")
```

### `await client.delete(table, query, protopass="default", ids=None)`

Delete records matching `query`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `table` | `str` | — | Target table |
| `query` | `dict \| str` | — | Match query |
| `protopass` | `str` | `"default"` | Proto-pass profile |
| `ids` | `list[str]` | `[]` | Explicit record IDs |

```python
await client.delete("users", {"active": False})
```

### `await client.onql(query, protopass="default", ctxkey="", ctxvalues=None)`

Run a raw ONQL query. The server's `{error, data}` envelope is unwrapped.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | — | ONQL query text |
| `protopass` | `str` | `"default"` | Proto-pass profile |
| `ctxkey` | `str` | `""` | Context key |
| `ctxvalues` | `list[str]` | `[]` | Context values |

```python
rows = await client.onql('select * from users where age > 18')
```

### `client.build(query, *values)`

Replace `$1`, `$2`, … placeholders with values. Strings are automatically
double-quoted; numbers and booleans are inlined verbatim.

```python
q = client.build('select * from users where name = $1 and age > $2', "John", 18)
# -> 'select * from users where name = "John" and age > 18'
rows = await client.onql(q)
```

### Full example

```python
import asyncio
from onqlclient import ONQLClient

async def main():
    client = await ONQLClient.create("localhost", 5656)
    client.setup("mydb")

    await client.insert("users", {"name": "John", "age": 30})

    rows = await client.onql(
        client.build("select * from users where age >= $1", 18)
    )
    print(rows)

    await client.update("users", {"age": 31}, {"name": "John"})
    await client.delete("users", {"name": "John"})
    await client.close()

asyncio.run(main())
```

## Protocol

The client communicates over TCP using a delimiter-based message format:

```
<request_id>\x1E<keyword>\x1E<payload>\x04
```

- `\x1E` — field delimiter
- `\x04` — end-of-message marker

## License

MIT
