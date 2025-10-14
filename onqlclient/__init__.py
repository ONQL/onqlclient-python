# onqlclient/__init__.py
import asyncio
import json
import keyword
import socket
import logging
import uuid
from typing import Dict, Optional, Callable, Any

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - ASYNC_CLIENT - %(levelname)s - %(message)s')

class ONQLClient:
	"""
	An asynchronous, concurrent-safe Python client for the ONQL Go TCP server.
	"""
	def __init__(self):
		self._reader: Optional[asyncio.StreamReader] = None
		self._writer: Optional[asyncio.StreamWriter] = None
		self._reader_task: Optional[asyncio.Task] = None
		self.pending_requests: Dict[str, asyncio.Future] = {}
		self._EOM = b'\x04'  # End-of-Message character
		self._DELIMITER = '\x1E' # Field delimiter character
		self.subscriptions: Dict[str, Callable[[str, str, str], Any]] = {}

	@classmethod
	async def create(cls, host: str = "localhost", port: int = 5656):
		self = cls()
		try:
			self._reader, self._writer = await asyncio.open_connection(host, port, limit=16*1024*1024)
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

	async def send_request(self, keyword: str, payload: str, timeout: int = 10) -> dict:
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
