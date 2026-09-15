"""Mock OpenWebNet gateway harness for testing network and protocol interactions."""
import asyncio
from typing import Callable, List, Optional


class MockGatewayHarness:
    """Simulates an OpenWebNet gateway TCP server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, password: Optional[str] = None):
        self.host = host
        self.port = port
        self.password = password
        self.server: Optional[asyncio.Server] = None
        self.received_messages: List[str] = []
        self.connected_clients: List[asyncio.StreamWriter] = []
        self._custom_handler: Optional[Callable[[str, asyncio.StreamWriter], Optional[str]]] = None
        self._disconnect_on_connect: bool = False
        self._nack_commands: bool = False
        self._response_delay: float = 0.0
        self._disconnect_after_messages: Optional[int] = None
        self._messages_processed: int = 0

    async def start(self) -> int:
        """Start the mock gateway server and return bound port."""
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self):
        """Stop server and disconnect all clients."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        await self.disconnect_all_clients()

    async def disconnect_all_clients(self):
        """Sever all currently connected client sockets immediately."""
        for writer in list(self.connected_clients):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self.connected_clients.clear()

    def set_custom_handler(self, handler: Callable[[str, asyncio.StreamWriter], Optional[str]]):
        """Register custom protocol handler."""
        self._custom_handler = handler

    def set_disconnect_on_connect(self, disconnect: bool):
        """Force immediate client disconnection upon connect for failure testing."""
        self._disconnect_on_connect = disconnect

    def set_nack_commands(self, nack: bool):
        """Configure harness to send NACK (*#*0##) instead of ACK (*#*1##) for commands."""
        self._nack_commands = nack

    def set_response_delay(self, delay: float):
        """Simulate network latency or slow gateway response."""
        self._response_delay = delay

    def set_disconnect_after_messages(self, count: Optional[int]):
        """Disconnect client after processing a specified count of messages."""
        self._disconnect_after_messages = count

    def reset(self):
        """Reset state and counters."""
        self.received_messages.clear()
        self._messages_processed = 0
        self._disconnect_on_connect = False
        self._nack_commands = False
        self._response_delay = 0.0
        self._disconnect_after_messages = None

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.connected_clients.append(writer)
        if self._disconnect_on_connect:
            writer.close()
            await writer.wait_closed()
            return

        try:
            # Initial OpenWebNet ACK when connection opens
            writer.write(b"*#*1##")
            await writer.drain()

            buffer = ""
            while not reader.at_eof():
                data = await reader.read(1024)
                if not data:
                    break
                buffer += data.decode("ascii", errors="ignore")
                while "##" in buffer:
                    frame, buffer = buffer.split("##", 1)
                    msg = frame + "##"
                    self.received_messages.append(msg)
                    self._messages_processed += 1

                    if self._response_delay > 0:
                        await asyncio.sleep(self._response_delay)

                    if (
                        self._disconnect_after_messages is not None
                        and self._messages_processed >= self._disconnect_after_messages
                    ):
                        writer.close()
                        await writer.wait_closed()
                        return

                    if self._custom_handler:
                        response = self._custom_handler(msg, writer)
                        if response:
                            writer.write(response.encode("ascii"))
                            await writer.drain()
                        continue

                    # Standard OpenWebNet session handshakes
                    if msg == "*99*0##":
                        # Command session handshake
                        writer.write(b"*#*1##")
                        await writer.drain()
                    elif msg == "*99*1##":
                        # Event session handshake
                        writer.write(b"*#*1##")
                        await writer.drain()
                    elif msg.startswith("*#"):
                        # Status request -> ACK or NACK
                        resp = b"*#*0##" if self._nack_commands else b"*#*1##"
                        writer.write(resp)
                        await writer.drain()
                    else:
                        # Regular command -> ACK or NACK
                        resp = b"*#*0##" if self._nack_commands else b"*#*1##"
                        writer.write(resp)
                        await writer.drain()
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            if writer in self.connected_clients:
                self.connected_clients.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def broadcast_event(self, event_str: str):
        """Simulate sending an event from the gateway to all connected event listeners."""
        payload = event_str.encode("ascii")
        for writer in list(self.connected_clients):
            try:
                writer.write(payload)
                await writer.drain()
            except Exception:
                pass
