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

"""Client for communicating with mod-host via TCP socket."""

import logging
import socket
from typing import Any


class ModHostSocket:
    """Client for communicating with mod-host via TCP socket."""

    def __init__(self, host: str = 'localhost', port: int = 5555) -> None:
        """
        Initialize mod-host socket client.

        Args:
            host: mod-host hostname (default: localhost)
            port: mod-host socket port (default: 5555)
        """
        self.host: str = host
        self.port: int = port
        self.sock: socket.socket | None = None

    def connect(self) -> None:
        """Establish connection to mod-host socket."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logging.info(f"Connected to mod-host at {self.host}:{self.port}")
        except Exception as e:
            logging.error(f"Failed to connect to mod-host: {e}")
            raise

    def close(self) -> None:
        """Close connection to mod-host socket."""
        if self.sock:
            self.sock.close()
            self.sock = None
            logging.debug("Closed mod-host socket connection")

    def send_command(self, cmd: str) -> str:
        """
        Send command to mod-host and return response.

        Args:
            cmd: Command string to send

        Returns:
            Response string from mod-host

        Raises:
            RuntimeError: If not connected or command fails
        """
        if not self.sock:
            raise RuntimeError("Not connected to mod-host")

        try:
            self.sock.sendall(f"{cmd}\n".encode())
            response = self.sock.recv(4096).decode().strip()
            logging.debug(f"mod-host command: {cmd} -> {response}")
            return response
        except Exception as e:
            logging.error(f"mod-host command failed: {cmd} ({e})")
            raise

    def midi_map(self, instance: int, symbol: str, channel: int, cc: int,
                 minimum: float, maximum: float) -> str:
        """
        Map MIDI CC to parameter.

        Args:
            instance: Plugin instance number (e.g., 0)
            symbol: Parameter symbol (e.g., "gain")
            channel: MIDI channel (0-15)
            cc: MIDI CC number (0-127)
            minimum: Minimum parameter value
            maximum: Maximum parameter value

        Returns:
            Response from mod-host
        """
        cmd = f'midi_map {instance} {symbol} {channel} {cc} {minimum} {maximum}'
        return self.send_command(cmd)

    def midi_unmap(self, instance: int, symbol: str) -> str:
        """
        Remove MIDI CC mapping from parameter.

        Args:
            instance: Plugin instance number
            symbol: Parameter symbol

        Returns:
            Response from mod-host
        """
        cmd = f'midi_unmap {instance} {symbol}'
        return self.send_command(cmd)

    def __enter__(self) -> 'ModHostSocket':
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()
