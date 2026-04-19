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

    # Insert a single record
    await client.insert("mydb", "users", {"id": "u1", "name": "John", "age": 30})

    # Read via an ONQL expression
    rows = await client.onql('mydb.users[age>18]')
    print(rows)

    # Update via a query string
    await client.update(
        "mydb", "users",
        {"age": 31},
        client.build('mydb.users[id=$1].id', "u1"),
    )

    # ...or via explicit ids
    await client.delete("mydb", "users", "", ids=["u1"])

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

Sends a request and waits for a response. Returns a dict with `request_id`,
`source`, and `payload`.

### `await client.close()`

Closes the connection.

## Direct ORM-style API

On top of `send_request`, the client ships convenience methods that build the
standard payload envelopes for common operations and unwrap the `{error, data}`
response automatically.

`db` is passed explicitly to `insert` / `update` / `delete`. `onql` takes a
fully-qualified ONQL expression (which already includes the db name), so no
separate db argument is needed.

`query` arguments are **ONQL expression strings**, e.g.
`'mydb.users[id="u1"].id'` or `'mydb.orders[status="pending"]'`. Use
`client.build(template, *values)` to substitute `$1, $2, ...` — strings get
double-quoted, numbers/booleans are inlined verbatim.

### `await client.insert(db, table, data)`

Insert a **single** record.

| Parameter | Type | Description |
|-----------|------|-------------|
| `db` | `str` | Database name |
| `table` | `str` | Target table |
| `data` | `dict` | A single record object |

```python
await client.insert("mydb", "users", {"id": "u1", "name": "John", "age": 30})
```

### `await client.update(db, table, data, query="", protopass="default", ids=None)`

Update records matching `query` (or the explicit `ids`).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `db` | `str` | — | Database name |
| `table` | `str` | — | Target table |
| `data` | `dict` | — | Fields to update |
| `query` | `str` | `""` | ONQL query expression. Pass `""` when using `ids`. |
| `protopass` | `str` | `"default"` | Proto-pass profile |
| `ids` | `list[str]` | `None` | Explicit record IDs (alternative to `query`) |

```python
await client.update(
    "mydb", "users",
    {"age": 31},
    client.build('mydb.users[id=$1].id', "u1"),
)

await client.update("mydb", "users", {"age": 31}, "", ids=["u1"])
```

### `await client.delete(db, table, query="", protopass="default", ids=None)`

Delete records matching `query` (or the explicit `ids`).

```python
await client.delete("mydb", "users",
    client.build('mydb.users[id=$1].id', "u1"))

await client.delete("mydb", "users", "", ids=["u1"])
```

### `await client.onql(query, protopass="default", ctxkey="", ctxvalues=None)`

Run a raw ONQL query. The server's `{error, data}` envelope is unwrapped.

```python
rows = await client.onql('mydb.users[age>18]')

# With $-placeholder interpolation:
by_name = await client.onql(
    client.build('mydb.users[name=$1]', "John"))
```

### `client.build(query, *values)`

Replace `$1`, `$2`, … placeholders with values. Strings are automatically
double-quoted; numbers and booleans are inlined verbatim.

```python
q = client.build('mydb.users[name=$1 and age>$2]', "John", 18)
# -> 'mydb.users[name="John" and age>18]'
rows = await client.onql(q)
```

## Protocol

```
<request_id>\x1E<keyword>\x1E<payload>\x04
```

- `\x1E` — field delimiter
- `\x04` — end-of-message marker

## License

MIT
