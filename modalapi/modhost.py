# This file is part of pi-stomp.
#
# pi-stomp is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# pi-stomp is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pi-stomp.  If not, see <https://www.gnu.org/licenses/>.

"""Client for communicating with mod-host via TCP socket.

mod-host uses a dual-socket architecture:
- Command socket (default port 5555): Send commands TO mod-host
- Feedback socket (default port 5556): Receive responses FROM mod-host

Start mod-host with: mod-host -p 5555 -f 5556
"""

import logging
import socket
from typing import Any


class ModHostSocket:
    """Client for communicating with mod-host via dual TCP sockets.

    mod-host requires two separate socket connections:
    1. Command socket (port): For sending commands
    2. Feedback socket (port+1): For receiving responses

    Example:
        with ModHostSocket() as sock:
            sock.midi_map(0, "gain", 0, 75, 0.0, 1.0)
    """

    def __init__(self, host: str = 'localhost', port: int = 5555) -> None:
        """
        Initialize mod-host socket client.

        Args:
            host: mod-host hostname (default: localhost)
            port: mod-host command port (default: 5555)
        """
        self.host: str = host
        self.port: int = port
        self.feedback_port: int = port + 1

        self.command_sock: socket.socket | None = None
        self.feedback_sock: socket.socket | None = None

    def connect(self) -> None:
        """Establish connections to mod-host command and feedback sockets."""
        try:
            self.command_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.command_sock.connect((self.host, self.port))
            logging.debug(f"Connected to mod-host command socket at {self.host}:{self.port}")
        except Exception as e:
            self._cleanup()
            raise ConnectionError(f"Failed to connect to mod-host command socket: {e}") from e

        try:
            self.feedback_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.feedback_sock.connect((self.host, self.feedback_port))
            self.feedback_sock.settimeout(1.0)
            logging.debug(f"Connected to mod-host feedback socket at {self.host}:{self.feedback_port}")
        except Exception as e:
            logging.warning(
                f"Feedback socket unavailable at port {self.feedback_port}. "
                f"Start mod-host with: mod-host -p {self.port} -f {self.feedback_port}"
            )
            self.feedback_sock = None

    def close(self) -> None:
        """Close connections to mod-host sockets."""
        self._cleanup()

    def _cleanup(self) -> None:
        """Internal cleanup of socket resources."""
        if self.command_sock:
            try:
                self.command_sock.close()
            except Exception:
                pass
            self.command_sock = None

        if self.feedback_sock:
            try:
                self.feedback_sock.close()
            except Exception:
                pass
            self.feedback_sock = None

    def send_command(self, cmd: str) -> None:
        """
        Send command to mod-host (fire-and-forget).

        Args:
            cmd: Command string to send

        Raises:
            RuntimeError: If not connected to command socket
        """
        if not self.command_sock:
            raise RuntimeError("Not connected to mod-host command socket")

        try:
            self.command_sock.sendall(f"{cmd}\n".encode())
            logging.debug(f"Sent command: {cmd}")
        except Exception as e:
            raise RuntimeError(f"Failed to send command '{cmd}': {e}") from e

    def recv_response(self) -> str:
        """
        Receive response from mod-host feedback socket.

        Returns:
            Response string (e.g., "resp 0" for success)

        Raises:
            RuntimeError: If feedback socket unavailable or read fails
        """
        if not self.feedback_sock:
            raise RuntimeError("Feedback socket not available")

        try:
            response = self.feedback_sock.recv(4096).decode().strip()
            logging.debug(f"Received response: {response}")
            return response
        except socket.timeout:
            raise RuntimeError("Timeout waiting for mod-host response") from None
        except Exception as e:
            raise RuntimeError(f"Failed to receive response: {e}") from e

    def send_command_with_response(self, cmd: str) -> str:
        """
        Send command and wait for response.

        Args:
            cmd: Command string to send

        Returns:
            Response string from mod-host

        Raises:
            RuntimeError: If command fails or response unavailable
        """
        self.send_command(cmd)
        return self.recv_response()

    def midi_map(self, instance: int, symbol: str, channel: int, cc: int,
                 minimum: float, maximum: float) -> None:
        """
        Map MIDI CC to parameter.

        Args:
            instance: Plugin instance number (e.g., 0)
            symbol: Parameter symbol (e.g., "gain")
            channel: MIDI channel (0-15)
            cc: MIDI CC number (0-127)
            minimum: Minimum parameter value
            maximum: Maximum parameter value
        """
        cmd = f'midi_map {instance} {symbol} {channel} {cc} {minimum} {maximum}'
        self.send_command(cmd)

    def midi_unmap(self, instance: int, symbol: str) -> None:
        """
        Remove MIDI CC mapping from parameter.

        Args:
            instance: Plugin instance number
            symbol: Parameter symbol
        """
        cmd = f'midi_unmap {instance} {symbol}'
        self.send_command(cmd)

    def __enter__(self) -> 'ModHostSocket':
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()
