from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Dict, Any, List

from ..common import WebRequest

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .application import InternalTransport as ITransport
    from .klippy_connection import KlippyConnection as Klippy
    from .klippy_apis import KlippyAPI


class RfidManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.name = config.get_name()

        # Get number of RFID channels (default 4 for U1)
        self.channel_count = config.getint('channels', 4, minval=1, maxval=4)

        logging.info(f"RFID Manager: Initialized with {self.channel_count} channels")

        # Register API endpoints
        self.server.register_endpoint(
            "/server/rfid/tags", ["GET"], self._handle_get_tags
        )
        self.server.register_endpoint(
            "/server/rfid/tags/{channel}", ["GET"], self._handle_get_tag
        )
        self.server.register_endpoint(
            "/server/rfid/write_openspool", ["POST"], self._handle_write_openspool
        )
        self.server.register_endpoint(
            "/server/rfid/erase", ["POST"], self._handle_erase
        )
        self.server.register_endpoint(
            "/server/rfid/ui", ["GET"], self._handle_serve_ui,
            wrap_result=False, content_type="text/html"
        )
        self.server.register_endpoint(
            "/server/rfid/rfid-manager.css", ["GET"], self._handle_serve_css,
            wrap_result=False, content_type="text/css"
        )
        self.server.register_endpoint(
            "/server/rfid/rfid-manager.js", ["GET"], self._handle_serve_js,
            wrap_result=False, content_type="application/javascript"
        )

    async def component_init(self) -> None:
        """Called when Moonraker components are initialized."""
        self.klippy: Klippy = self.server.lookup_component("klippy_connection")
        self.klippy_apis: KlippyAPI = self.server.lookup_component("klippy_apis")
        logging.info("RFID Manager: Component initialized")

    async def _get_filament_detect_status(self) -> Dict[str, Any]:
        """Get status from Klipper's filament_detect module.

        Uses WebRequest for read-only state queries. Write operations
        use run_gcode() to execute Klipper commands.
        """
        req = WebRequest(
            endpoint="objects/query",
            args={"objects": {"filament_detect": None}},
        )
        result = await self.klippy.request(req)
        return result.get("status", {}).get("filament_detect", {})


    async def _read_tag(self, channel: int) -> Dict[str, Any]:
        """Read tag data for a specific channel."""
        try:
            # Get status from filament_detect module
            status = await self._get_filament_detect_status()
            info_list = status.get("info", [])

            if channel < 0 or channel >= len(info_list):
                return {
                    "channel": channel,
                    "tag_present": False,
                    "tag_type": None,
                    "uid": None,
                    "filament": {}
                }

            tag_info = info_list[channel]

            # Check if no tag is present (empty dict or no vendor)
            if not tag_info or not isinstance(tag_info, dict):
                return {
                    "channel": channel,
                    "tag_present": False,
                    "tag_type": None,
                    "uid": None,
                    "filament": {}
                }

            # Parse UID safely
            card_uid = tag_info.get("CARD_UID", [])
            if isinstance(card_uid, list) and card_uid:
                uid = ":".join(f"{b:02X}" for b in card_uid)
            else:
                uid = None

            # Check if tag is present but empty/uninitialized
            vendor = tag_info.get("VENDOR", "")
            has_vendor = vendor and vendor != "" and vendor.upper() != "NONE"

            if not has_vendor and uid:
                # Tag detected but not programmed
                return {
                    "channel": channel,
                    "tag_present": True,
                    "tag_empty": True,
                    "tag_type": tag_info.get("CARD_TYPE", "Unknown"),
                    "uid": uid,
                    "filament": {}
                }
            elif not has_vendor:
                # No tag present
                return {
                    "channel": channel,
                    "tag_present": False,
                    "tag_type": None,
                    "uid": None,
                    "filament": {}
                }

            # Tag present with data
            return {
                "channel": channel,
                "tag_present": True,
                "tag_empty": False,
                "tag_type": tag_info.get("CARD_TYPE", "Unknown"),
                "uid": uid,
                "filament": {
                    "brand": tag_info.get("VENDOR", ""),
                    "type": tag_info.get("MAIN_TYPE", ""),
                    "subtype": tag_info.get("SUB_TYPE", ""),
                    "diameter": tag_info.get("DIAMETER", 175) / 100.0,  # Convert to mm
                    "density": tag_info.get("DENSITY", 0.0),
                    "color_hex": f"{tag_info.get('RGB_1', 0xFFFFFF):06X}",
                    "min_temp": tag_info.get("HOTEND_MIN_TEMP", 0),
                    "max_temp": tag_info.get("HOTEND_MAX_TEMP", 0),
                    "bed_temp": tag_info.get("BED_TEMP", 0)
                }
            }

        except Exception as e:
            logging.error(f"RFID Manager: Failed to read tag on channel {channel}: {e}")
            return {
                "channel": channel,
                "tag_present": False,
                "error": str(e)
            }

    async def _handle_get_tags(self, web_request) -> Dict[str, Any]:
        """GET /server/rfid/tags - List all channels with tag status."""
        channels: List[Dict[str, Any]] = []

        for channel in range(self.channel_count):
            tag_data = await self._read_tag(channel)
            channels.append(tag_data)

        return {"channels": channels}

    async def _handle_get_tag(self, web_request) -> Dict[str, Any]:
        """GET /server/rfid/tags/{channel} - Get specific channel tag info."""
        channel = web_request.get_int("channel")

        if channel < 0 or channel >= self.channel_count:
            raise self.server.error(f"Invalid channel: {channel}. Must be 0-{self.channel_count-1}")

        return await self._read_tag(channel)

    async def _handle_write_openspool(self, web_request) -> Dict[str, Any]:
        """POST /server/rfid/write_openspool - Write OpenSpool format to NTAG tag."""
        import asyncio

        # Parse request parameters
        channel = web_request.get_int("channel", 0)
        material_type = web_request.get_str("type", "PLA").upper()
        brand = web_request.get_str("brand", "Generic")
        color_hex = web_request.get_str("color_hex", "FFFFFF").lstrip('#').upper()
        diameter = web_request.get_float("diameter", 1.75)

        # Optional parameters
        subtype = web_request.get_str("subtype", None)
        density = web_request.get_float("density", None)
        min_temp = web_request.get_int("min_temp", None)
        max_temp = web_request.get_int("max_temp", None)
        bed_temp = web_request.get_int("bed_temp", None)

        # Build G-code command
        gcode_cmd = f"FILAMENT_TAG_WRITE_OPENSPOOL CHANNEL={channel} TYPE={material_type} BRAND=\"{brand}\" COLOR={color_hex} DIAMETER={diameter}"

        if subtype:
            gcode_cmd += f" SUBTYPE=\"{subtype}\""
        if density is not None:
            gcode_cmd += f" DENSITY={density}"
        if min_temp is not None:
            gcode_cmd += f" MIN_TEMP={min_temp}"
        if max_temp is not None:
            gcode_cmd += f" MAX_TEMP={max_temp}"
        if bed_temp is not None:
            gcode_cmd += f" BED_TEMP={bed_temp}"

        try:
            # Execute G-code command via Klipper
            result = await self.klippy_apis.run_gcode(gcode_cmd)

            # Check for errors in the result
            if result and isinstance(result, str) and "!!" in result:
                # Error occurred
                error_msg = result.split("!!")[-1].strip() if "!!" in result else result
                return {
                    "success": False,
                    "error": error_msg
                }

            # Trigger tag refresh
            await self.klippy_apis.run_gcode(f"FILAMENT_DT_UPDATE CHANNEL={channel}")

            # Wait for refresh to complete
            await asyncio.sleep(0.5)

            # Verify written data by reading tag back
            tag_data = await self._read_tag(channel)

            if not tag_data.get("tag_present"):
                return {
                    "success": False,
                    "error": "Tag not detected after write. Please check tag placement."
                }

            if tag_data.get("tag_empty", True):
                return {
                    "success": False,
                    "error": "Tag appears empty after write. Write may have failed."
                }

            # Verify key fields match
            filament = tag_data.get("filament", {})
            written_type = filament.get("type", "").upper()
            written_brand = filament.get("brand", "")
            written_color = filament.get("color_hex", "").upper()

            verification_passed = (
                written_type == material_type and
                written_brand == brand and
                written_color == color_hex
            )

            if verification_passed:
                return {
                    "success": True,
                    "message": f"Tag written and verified successfully on channel {channel}",
                    "details": {
                        "channel": channel,
                        "type": material_type,
                        "brand": brand,
                        "color_hex": color_hex
                    },
                    "verified": True,
                    "tag_data": tag_data
                }
            else:
                # Write succeeded but verification shows mismatch
                return {
                    "success": True,
                    "message": f"Tag written on channel {channel}, but verification shows different data",
                    "details": {
                        "channel": channel,
                        "type": material_type,
                        "brand": brand,
                        "color_hex": color_hex
                    },
                    "verified": False,
                    "verification_mismatch": {
                        "expected_type": material_type,
                        "got_type": written_type,
                        "expected_brand": brand,
                        "got_brand": written_brand,
                        "expected_color": color_hex,
                        "got_color": written_color
                    },
                    "tag_data": tag_data
                }

        except Exception as e:
            logging.exception("Failed to write tag")
            return {
                "success": False,
                "error": f"Failed to write tag: {str(e)}"
            }

    async def _handle_erase(self, web_request) -> Dict[str, Any]:
        """POST /server/rfid/erase - Erase NTAG tag."""
        import asyncio

        # Parse request parameters
        channel = web_request.get_int("channel", 0)
        confirm = web_request.get_boolean("confirm", False)

        if not confirm:
            return {
                "success": False,
                "error": "Tag erase requires explicit confirmation (confirm=true)"
            }

        # Build G-code command
        gcode_cmd = f"FILAMENT_TAG_ERASE CHANNEL={channel} CONFIRM=1"

        try:
            # Execute G-code command via Klipper
            result = await self.klippy_apis.run_gcode(gcode_cmd)

            # Check for errors in the result
            if result and isinstance(result, str) and "!!" in result:
                # Error occurred
                error_msg = result.split("!!")[-1].strip() if "!!" in result else result
                return {
                    "success": False,
                    "error": error_msg
                }

            # Trigger tag refresh
            await self.klippy_apis.run_gcode(f"FILAMENT_DT_UPDATE CHANNEL={channel}")

            # Wait for refresh to complete
            await asyncio.sleep(0.5)

            # Verify tag is now empty
            tag_data = await self._read_tag(channel)

            if tag_data.get("tag_empty", False) or not tag_data.get("filament", {}).get("brand"):
                return {
                    "success": True,
                    "message": f"Tag erased and verified successfully on channel {channel}",
                    "details": {
                        "channel": channel
                    },
                    "verified": True,
                    "tag_data": tag_data
                }
            else:
                return {
                    "success": True,
                    "message": f"Tag erase command sent on channel {channel}, but tag still shows data",
                    "details": {
                        "channel": channel
                    },
                    "verified": False,
                    "tag_data": tag_data
                }

        except Exception as e:
            logging.exception("Failed to erase tag")
            return {
                "success": False,
                "error": f"Failed to erase tag: {str(e)}"
            }

    async def _handle_serve_ui(self, web_request):
        """GET /server/rfid/ui - Serve HTML UI."""
        return self._serve_static_file("/home/lava/www/rfid-manager.html")

    async def _handle_serve_css(self, web_request):
        """GET /server/rfid/rfid-manager.css - Serve CSS file."""
        return self._serve_static_file("/home/lava/www/rfid-manager.css")

    async def _handle_serve_js(self, web_request):
        """GET /server/rfid/rfid-manager.js - Serve JavaScript file."""
        return self._serve_static_file("/home/lava/www/rfid-manager.js")

    def _serve_static_file(self, file_path: str) -> str:
        """Helper to serve static files."""
        import os

        if not os.path.exists(file_path):
            raise self.server.error(f"File not found: {file_path}", 404)

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            raise self.server.error(f"Failed to read file: {e}", 500)


def load_component(config: ConfigHelper) -> RfidManager:
    return RfidManager(config)
