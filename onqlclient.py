# onqlclient.py
import asyncio
import json
import keyword
import socket
import logging
import uuid
from typing import Dict, Optional, Callable, Any, List

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - ASYNC_CLIENT - %(levelname)s - %(message)s')

class ONQLClient:
	"""
	An asynchronous, concurrent-safe Python client for the ONQL Go TCP server.
	"""
	def __init__(self, default_timeout: int = 10):
		self._reader: Optional[asyncio.StreamReader] = None
		self._writer: Optional[asyncio.StreamWriter] = None
		self._reader_task: Optional[asyncio.Task] = None
		self.pending_requests: Dict[str, asyncio.Future] = {}
		self._EOM = b'\x04'  # End-of-Message character
		self._DELIMITER = '\x1E' # Field delimiter character
		self.subscriptions: Dict[str, Callable[[str, str, str], Any]] = {}
		self._default_timeout = default_timeout
		self.db: Optional[str] = None

	@classmethod
	async def create(cls, host: str = "localhost", port: int = 5656, data_limit: int = 16 * 1024 * 1024, default_timeout: int = 10):
		"""
		Create and return a connected ONQLClient.

		Args:
			host:            Server hostname (default: "localhost").
			port:            Server port (default: 5656).
			data_limit:      Maximum bytes for a single read buffer (default: 16 MB).
			default_timeout: Default request timeout in seconds (default: 10).
			                 Can be overridden per-call in send_request().
		"""
		self = cls(default_timeout=default_timeout)
		try:
			self._reader, self._writer = await asyncio.open_connection(host, port, limit=data_limit)
			logging.info(f"Successfully connected to server at {host}:{port}")
		except socket.error as e:
			logging.error(f"Failed to connect to {host}:{port}. Error: {e}")
			raise ConnectionError(f"Could not connect to server: {e}") from e
		self._reader_task = asyncio.create_task(self._response_reader_loop())
		return self

	async def _response_reader_loop(self):
		while self._reader and not self._reader.at_eof():
			try:
				full_response_bytes = await self._reader.readuntil(self._EOM)
				full_response = full_response_bytes.rstrip(self._EOM).decode('utf-8')
				parts = full_response.split(self._DELIMITER)
				if len(parts) != 3:
					logging.warning(f"Received malformed response: {full_response}")
					continue
				response_rid, source_id, response_payload = parts

				# 1) Is this a live subscription frame?
				cb = self.subscriptions.get(response_rid)
				if cb:
					self._dispatch_subscription(response_rid, keyword, response_payload)
					continue

				future = self.pending_requests.get(response_rid)
				if future and not future.done():
					parsed_response = {
						"request_id": response_rid,
						"source": source_id,
						"payload": response_payload
					}
					future.set_result(parsed_response)
				else:
					logging.warning(f"Received response for unknown or already handled request ID: {response_rid}")
			except asyncio.IncompleteReadError:
				logging.error("Connection closed by server unexpectedly.")
				break
			except Exception as e:
				logging.error(f"Error in reader loop: {e}")
				break
		for rid, future in self.pending_requests.items():
			if not future.done():
				future.set_exception(ConnectionError("Connection lost."))

	async def close(self):
		if self._writer:
			self._writer.close()
			await self._writer.wait_closed()
			logging.info("Connection closed.")
		if self._reader_task:
			self._reader_task.cancel()
		self.pending_requests.clear()

	def _generate_request_id(self) -> str:
		return uuid.uuid4().hex[:8]

	async def send_request(self, keyword: str, payload: str, timeout: Optional[int] = None) -> dict:
		if timeout is None:
			timeout = self._default_timeout
		if not self._writer or self._writer.is_closing():
			raise ConnectionError("Client is not connected.")
		request_id = self._generate_request_id()
		future = asyncio.Future()
		self.pending_requests[request_id] = future
		try:
			message_to_send = f"{request_id}{self._DELIMITER}{keyword}{self._DELIMITER}{payload}".encode('utf-8') + self._EOM
			self._writer.write(message_to_send)
			await self._writer.drain()
			return await asyncio.wait_for(future, timeout=timeout)
		except asyncio.TimeoutError:
			logging.error(f"Request {request_id} timed out.")
			self.pending_requests.pop(request_id, None)
			raise
		finally:
			self.pending_requests.pop(request_id, None)

 	# -------- NEW: subscribe / unsubscribe --------

	async def subscribe(self, onquery: str, query: str, callback: Callable[[str, str, str], Any]) -> str:
			"""
			Open a streaming subscription. All future frames with this RID will be delivered to `callback`.

			Args:
				onquery: ONQL 'onquery' string (can be empty if server allows).
				query:   ONQL query string to compute the payload you want pushed.
				callback(rid, keyword, payload): function or async function.

			Returns:
				rid (str): the subscription request id (use with `unsubscribe` if needed).
			"""
			if not self._writer or self._writer.is_closing():
				raise ConnectionError("Client is not connected.")

			rid = self._generate_request_id()
			self.subscriptions[rid] = callback

			payload = json.dumps({"onquery": onquery, "query": query})
			frame = f"{rid}{self._DELIMITER}subscribe{self._DELIMITER}{payload}".encode("utf-8") + self._EOM
			self._writer.write(frame)
			await self._writer.drain()
			return rid

	async def unsubscribe(self, rid: str):
			"""
			Stop receiving events for a subscription. If your server supports an
			'unsubscribe' keyword, this sends it; otherwise we just drop the local callback.
			"""
			# remove local handler first (avoid any race delivering to a now 'dead' consumer)
			self.subscriptions.pop(rid, None)

			if not self._writer or self._writer.is_closing():
				return

			# Optional: tell server
			try:
				payload = json.dumps({"rid": rid}, separators=(",", ":"))
				frame = f"{rid}{self._DELIMITER}unsubscribe{self._DELIMITER}{payload}".encode("utf-8") + self._EOM
				self._writer.write(frame)
				await self._writer.drain()
			except Exception:
				# Even if this fails, we've removed the local handler.
				logging.debug("unsubscribe frame send failed (ignored)")


	def _dispatch_subscription(self, rid: str, keyword: str, payload: str):
			"""
			Schedule the subscription callback without blocking the reader loop.
			Supports both sync and async callbacks.
			"""
			cb = self.subscriptions.get(rid)
			if not cb:
				return
			try:
				result = cb(rid, keyword, payload)
				if asyncio.iscoroutine(result):
					asyncio.create_task(result)  # fire-and-forget
			except Exception:
				logging.exception("Error in subscription callback")

	# ------------------------------------------------------------------
	# Direct ORM-style API (insert / update / delete / onql / build)
	# ------------------------------------------------------------------

	def setup(self, db: str) -> "ONQLClient":
		"""Set the default database name used by insert/update/delete.

		Returns ``self`` so calls can be chained.
		"""
		self.db = db
		return self

	@staticmethod
	def _process_result(raw: str) -> Any:
		"""Parse the standard ``{error, data}`` envelope returned by the server.

		Raises ``Exception`` if ``error`` is truthy; otherwise returns ``data``.
		"""
		try:
			parsed = json.loads(raw)
		except Exception:
			raise Exception(raw)
		if parsed.get("error"):
			raise Exception(parsed["error"])
		return parsed.get("data")

	async def insert(self, table: str, data: Any) -> Any:
		"""Insert one record or a list of records into ``table``.

		Args:
			table: Target table name.
			data:  A single record dict or a list of record dicts.

		Returns:
			The parsed ``data`` field from the server response envelope.

		Raises:
			Exception if the server returned a non-empty ``error`` field.
		"""
		payload = json.dumps({"db": self.db, "table": table, "records": data})
		res = await self.send_request("insert", payload)
		return self._process_result(res["payload"])

	async def update(self, table: str, data: Any, query: Any,
	                 protopass: str = "default",
	                 ids: Optional[List[str]] = None) -> Any:
		"""Update records in ``table`` matching ``query``.

		Args:
			table:     Target table name.
			data:      Fields to update.
			query:     Match query.
			protopass: Proto-pass profile (default ``"default"``).
			ids:       Optional list of explicit record IDs.
		"""
		payload = json.dumps({
			"db": self.db,
			"table": table,
			"records": data,
			"query": query,
			"protopass": protopass,
			"ids": ids or [],
		})
		res = await self.send_request("update", payload)
		return self._process_result(res["payload"])

	async def delete(self, table: str, query: Any,
	                 protopass: str = "default",
	                 ids: Optional[List[str]] = None) -> Any:
		"""Delete records in ``table`` matching ``query``."""
		payload = json.dumps({
			"db": self.db,
			"table": table,
			"query": query,
			"protopass": protopass,
			"ids": ids or [],
		})
		res = await self.send_request("delete", payload)
		return self._process_result(res["payload"])

	async def onql(self, query: str, protopass: str = "default",
	               ctxkey: str = "",
	               ctxvalues: Optional[List[str]] = None) -> Any:
		"""Execute a raw ONQL query and return the decoded ``data`` payload.

		Raises ``Exception`` on server-reported error.
		"""
		payload = json.dumps({
			"query": query,
			"protopass": protopass,
			"ctxkey": ctxkey,
			"ctxvalues": ctxvalues or [],
		})
		res = await self.send_request("onql", payload)
		return self._process_result(res["payload"])

	def build(self, query: str, *values: Any) -> str:
		"""Replace ``$1``, ``$2``, ... placeholders with ``values``.

		Strings are double-quoted; numbers and booleans are inlined verbatim.
		Useful for composing ONQL queries safely from application-level values.
		"""
		for i, value in enumerate(values):
			placeholder = "$" + str(i + 1)
			if isinstance(value, bool):
				replacement = "true" if value else "false"
			elif isinstance(value, str):
				replacement = '"' + value + '"'
			else:
				replacement = str(value)
			query = query.replace(placeholder, replacement)
		return query
