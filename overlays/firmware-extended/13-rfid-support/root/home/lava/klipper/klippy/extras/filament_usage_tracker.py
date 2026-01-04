"""Filament Usage Tracker - Tracks filament consumption and updates RFID tags.

This module integrates with print_stats to calculate filament usage based on
extruded length, diameter, and density. At the end of each print, it updates
the RFID tag with the new current_weight and cumulative_usage.
"""

import logging
import math

class FilamentUsageTracker:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()

        # Get references to other modules
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')

        # Will be set when modules are available
        self.print_stats = None
        self.filament_detect = None

        # Register for print events
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

        # Register G-Code commands
        self.gcode.register_command(
            'FILAMENT_USAGE_REPORT',
            self.cmd_FILAMENT_USAGE_REPORT,
            desc=self.cmd_FILAMENT_USAGE_REPORT_help
        )

        logging.info("FilamentUsageTracker initialized")

    def _handle_ready(self):
        """Called when Klipper is ready - get module references and register events."""
        try:
            self.print_stats = self.printer.lookup_object('print_stats')
            self.filament_detect = self.printer.lookup_object('filament_detect')

            # Register for print completion events
            self.printer.register_event_handler("print_stats:stop", self._handle_print_stop)

            logging.info("FilamentUsageTracker ready, registered for print_stats:stop events")
        except Exception as e:
            logging.error("FilamentUsageTracker failed to initialize: %s", str(e))

    def _handle_print_stop(self, eventtime):
        """Handle print stop event - calculate usage and update RFID tags."""
        try:
            if not self.print_stats or not self.filament_detect:
                logging.warning("FilamentUsageTracker: modules not available")
                return

            # Get print stats
            stats = self.print_stats.get_status(eventtime)
            filament_used_mm = stats.get('filament_used', 0.0)

            if filament_used_mm <= 0:
                logging.info("No filament used in this print, skipping RFID update")
                return

            logging.info("Print complete, filament used: %.2f mm", filament_used_mm)

            # Update each channel that has filament loaded
            # Query the number of channels from filament_detect
            channel_count = getattr(self.filament_detect, '_channel_nums', 4)
            for channel in range(channel_count):
                self._update_channel(channel, filament_used_mm, eventtime)

        except Exception as e:
            logging.exception("Error in FilamentUsageTracker print_stop handler: %s", str(e))

    def _update_channel(self, channel, filament_used_mm, eventtime):
        """Update filament weight for a specific channel."""
        try:
            # Get filament info for this channel
            error, filament_info = self.filament_detect.get_a_filament_info(channel)

            if error != 0 or not filament_info:
                logging.debug("No filament info for channel %d, skipping", channel)
                return

            # Extract required fields
            diameter_mm = filament_info.get('DIAMETER', 175) / 100.0  # Convert from 1/100mm to mm
            density_g_cm3 = filament_info.get('DENSITY', 0.0)
            current_weight_g = filament_info.get('CURRENT_WEIGHT', 0)
            full_weight_g = filament_info.get('FULL_WEIGHT', 0)
            cumulative_usage_g = filament_info.get('CUMULATIVE_USAGE', 0)

            # Skip if no density or weight data
            if density_g_cm3 <= 0 or full_weight_g <= 0:
                logging.info("Channel %d: No density/weight data, skipping update", channel)
                return

            # Calculate volume and weight used
            radius_mm = diameter_mm / 2.0
            area_mm2 = math.pi * radius_mm * radius_mm
            volume_mm3 = area_mm2 * filament_used_mm
            volume_cm3 = volume_mm3 / 1000.0
            weight_used_g = volume_cm3 * density_g_cm3

            # Calculate new values
            new_current_weight = max(0, current_weight_g - int(weight_used_g))
            new_cumulative_usage = cumulative_usage_g + int(weight_used_g)

            logging.info(
                "Channel %d: Used %.1f g (%.2f mm @ %.2f mm dia, %.2f g/cm³), "
                "current: %d -> %d g, cumulative: %d g",
                channel, weight_used_g, filament_used_mm, diameter_mm, density_g_cm3,
                current_weight_g, new_current_weight, new_cumulative_usage
            )

            # Update RFID tag
            success = self.filament_detect.request_update_filament_weight(
                channel,
                density_g_cm3,
                full_weight_g,
                new_current_weight,
                new_cumulative_usage
            )

            if success:
                logging.info("Channel %d: RFID tag updated successfully", channel)

                # Check for low filament warning
                if new_current_weight > 0:
                    percent_remaining = (new_current_weight / full_weight_g) * 100
                    if percent_remaining < 10:
                        self.gcode.respond_info(
                            f"WARNING: Channel {channel} filament low: "
                            f"{new_current_weight}g remaining ({percent_remaining:.1f}%)"
                        )
            else:
                logging.error("Channel %d: Failed to update RFID tag", channel)

        except Exception as e:
            logging.exception("Error updating channel %d: %s", channel, str(e))

    # G-Code command for manual query
    cmd_FILAMENT_USAGE_REPORT_help = "Report current filament usage status from RFID tags"

    def cmd_FILAMENT_USAGE_REPORT(self, gcmd):
        """Report current filament status for all channels."""
        try:
            eventtime = self.reactor.monotonic()

            # Query the number of channels from filament_detect
            channel_count = getattr(self.filament_detect, '_channel_nums', 4)
            for channel in range(channel_count):
                error, filament_info = self.filament_detect.get_a_filament_info(channel)

                if error != 0 or not filament_info or not filament_info.get('VENDOR'):
                    gcmd.respond_info(f"Channel {channel}: No filament detected")
                    continue

                vendor = filament_info.get('VENDOR', 'Unknown')
                material_type = filament_info.get('MAIN_TYPE', 'Unknown')
                diameter_mm = filament_info.get('DIAMETER', 0) / 100.0
                density = filament_info.get('DENSITY', 0.0)
                full_weight = filament_info.get('FULL_WEIGHT', 0)
                current_weight = filament_info.get('CURRENT_WEIGHT', 0)
                cumulative_usage = filament_info.get('CUMULATIVE_USAGE', 0)

                if full_weight > 0:
                    percent_used = (cumulative_usage / full_weight) * 100
                    percent_remaining = (current_weight / full_weight) * 100
                else:
                    percent_used = 0
                    percent_remaining = 100

                gcmd.respond_info(
                    f"Channel {channel}: {vendor} {material_type} "
                    f"({diameter_mm:.2f}mm, {density:.2f}g/cm³)"
                )
                gcmd.respond_info(
                    f"  Full: {full_weight}g, Current: {current_weight}g "
                    f"({percent_remaining:.1f}% remaining)"
                )
                gcmd.respond_info(
                    f"  Total used: {cumulative_usage}g ({percent_used:.1f}% of spool)"
                )

        except Exception as e:
            gcmd.respond_info(f"Error querying filament status: {e}")
            logging.exception("Error in FILAMENT_USAGE_REPORT: %s", str(e))

def load_config(config):
    return FilamentUsageTracker(config)
