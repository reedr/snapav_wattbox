"""Stewart Wattbox Device."""

import asyncio
import logging
import re

from homeassistant.core import HomeAssistant, callback

from .const import (
    WATTBOX_CONNECT_TIMEOUT,
    WATTBOX_PORT,
    WATTBOX_RESPONSE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

DEVICE_MODEL = "Model"
DEVICE_ON = "ON"
DEVICE_OFF = "OFF"
DEVICE_RESET = "RESET"
DEVICE_OUTLET_STATUS = "OutletStatus"
DEVICE_OUTLET_NAME = "OutletName"
DEVICE_OUTLET_SET = "OutletSet"
DEVICE_SERIAL = "Serial"
DEVICE_TOGGLE = "TOGGLE"

class WattboxDevice:
    """Represents a single Wattbox device."""

    def __init__(self, hass: HomeAssistant, host: str, username: str, password: str) -> None:
        """Set up class."""

        self._hass = hass
        self._host = host
        self._username = username
        self._password = password
        self._device_id = None
        self._reader: asyncio.StreamReader
        self._writer: asyncio.StreamWriter
        self._init_event = asyncio.Event()
        self._online = False
        self._callback = None
        self._listener = None
        self._data = {}
        self._outlet_name = []
        self._outlet_status = []
        self._response_re = re.compile("((#)(.+)|(OK)|\\?([^=]+)=(.+))")

    @property
    def device_id(self) -> str:
        """Use the mac."""
        return self._device_id

    @property
    def online(self) -> bool:
        """Return status."""
        return self._online

    @property
    def get_data_value(self, key: str) -> str:
        """Return a string value."""
        return self._data.get(key)

    async def open_connection(self, test: bool = False) -> bool:
        """Establish a connection."""
        if self.online:
            return True

        try:
            _LOGGER.debug("Establish new connection")
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, WATTBOX_PORT),
                timeout=WATTBOX_CONNECT_TIMEOUT,
            )
            await self._reader.readuntil("Username: ")
            self.send_to_device(self._username)
            await self._reader.readuntil("Password: ")
            self.send_to_device(self._password)
            if await self.query_and_response(DEVICE_MODEL):
                if await self.query_and_response(DEVICE_SERIAL):
                    if await self.query_and_response(DEVICE_OUTLET_NAME):
                        self._online = True
            if test:
                self._writer.close()
            else:
                self._online = True
                self._listener = asyncio.create_task(self.listener())

        except (TimeoutError, OSError, asyncio.IncompleteReadError) as err:
            self._online = False
            _LOGGER.error("Connect sequence error %s", err)
            raise ConnectionError("Connect sequence error") from err

        return True

    async def send_to_device(self, reqstr: str) -> None:
        """Make an API call."""
        if await self.open_connection():
            _LOGGER.debug("-> %s", reqstr)
            self._writer.write(reqstr.encode("ascii"))

    async def send_query(self, method: str) -> None:
        """Format and send command."""
        reqstr = f"?{method}\n"
        await self.send_to_device(reqstr)

    async def send_command(self, method: str, data: str) -> None:
        """Format and send command."""
        reqstr = f"!{method}={data}\n"
        await self.send_to_device(reqstr)

    def decode_response(self, resp: str) -> bool:
        """Decode the response."""
        respstr = resp.decode("ascii")
        _LOGGER.debug("<- %s", respstr)
        m = self._response_re.match(respstr)
        if m is None:
            return None
        result = m.group(2)
        if result == "OK":
            return True
        elif result == "#":
            return False
        else:
            method = m.group(2)
            data = m.group(3)
            self._data[method] = data
            if method == DEVICE_OUTLET_NAME:
                self._outlet_name = data.split(',')
            elif method == DEVICE_OUTLET_STATUS:
                self._outlet_status = [x == "1" for x in data.split(",")]
            elif method == DEVICE_SERIAL:
                self._device_id = data
            return True

    async def query_and_response(self, method: str) -> bool:
        """Send a query and wait for reponse."""
        self.send_query(method)
        try:
            devresp = await asyncio.wait_for(
                self._reader.readline(), timeout=WATTBOX_RESPONSE_TIMEOUT
            )
            return self.decode_response(devresp)

        except TimeoutError:
            return False

    async def test_connection(self) -> bool:
        """Test a connect."""
        return await self.open_connection(test=True)

    async def update_data(self) -> None:
        """Stuff that has to be polled."""
        self.send_query(DEVICE_OUTLET_STATUS)

    async def async_init(self, data_callback: callback) -> None:
        """Query position and wait for response."""
        self._callback = data_callback
        await self.update_data()

    async def listener(self) -> None:
        """Listen for status updates from device."""

        while True:
            buf = await self._reader.readline()
            if len(buf) == 0:
                _LOGGER.error("Connection closed")
                break
            if self.decode_response(buf):
                self._init_event.set()
                if self._callback is not None:
                    self._callback(self._data)

        self._writer.close()
        self._online = False

    @property
    def is_on(self, index: int) -> bool:
        """Property power."""
        return self._outlet_status[index]

    @property
    def outlet_names(self) -> list[str]:
        """Return source list."""
        return self._outlet_name

    async def async_outlet_set(self, index: int, op: str):
        """Set outlet status."""
        await self.send_command(DEVICE_OUTLET_SET,f"{index},{op}")

    async def async_turn_on(self, index: int):
        """Device turn on."""
        await self.async_outlet_set(index, DEVICE_ON)

    async def async_turn_off(self, index: int):
        """Device turn off."""
        await self.async_outlet_set(index, DEVICE_OFF)

    async def async_reset(self, index: int):
        """Device turn off then on."""
        await self.async_outlet_set(index, DEVICE_RESET)

    async def async_toggle(self, index: int):
        """Toggle device."""
        await self.async_outlet_set(index, DEVICE_TOGGLE)