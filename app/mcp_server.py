import logging
import os
import json
from fastmcp import FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("smartlife-mcp")

# Instantiate FastMCP server
mcp = FastMCP("SmartLife Ambient Controller MCP Server")

# Simulated database of home devices
DEVICES = {
    "front_door_lock": {"name": "Front Door Lock", "type": "lock", "status": "locked"},
    "living_room_light": {"name": "Living Room Light", "type": "light", "status": "off", "brightness": "70%"},
    "security_camera": {"name": "Security Camera", "type": "camera", "status": "armed"},
    "thermostat": {"name": "Main Thermostat", "type": "thermostat", "temperature": "72F", "mode": "cool"}
}

@mcp.tool()
def list_connected_devices() -> str:
    """List all connected smart devices in the house and their types.
    
    Returns:
        A JSON string containing the list of devices and details.
    """
    logger.info("🛠️ MCP Tool: 'list_connected_devices' called")
    return json.dumps(DEVICES, indent=2)

@mcp.tool()
def get_device_status(device_id: str) -> str:
    """Get the current status of a specific smart home device.
    
    Args:
        device_id: The ID of the device (e.g. 'front_door_lock', 'living_room_light', 'security_camera', 'thermostat').
        
    Returns:
        A string describing the device's status.
    """
    logger.info(f"🛠️ MCP Tool: 'get_device_status' called for '{device_id}'")
    if device_id not in DEVICES:
        return f"Error: Device '{device_id}' not found."
    return f"Device '{device_id}' status: {DEVICES[device_id]}"

@mcp.tool()
def set_device_state(device_id: str, state: str, value: str = None) -> str:
    """Set the state of a specific smart home device.
    
    Args:
        device_id: The ID of the device.
        state: The target state (e.g., 'on', 'off', 'locked', 'unlocked', 'armed', 'disarmed').
        value: Optional value for settings like temperature or brightness (e.g., '72F', '50%').
        
    Returns:
        A confirmation message.
    """
    logger.info(f"🛠️ MCP Tool: 'set_device_state' called for '{device_id}' -> state='{state}', value='{value}'")
    if device_id not in DEVICES:
        return f"Error: Device '{device_id}' not found."
    
    device = DEVICES[device_id]
    if device["type"] == "lock" and state not in ["locked", "unlocked"]:
        return f"Error: Invalid state '{state}' for lock. Use 'locked' or 'unlocked'."
    if device["type"] == "light" and state not in ["on", "off"]:
        return f"Error: Invalid state '{state}' for light. Use 'on' or 'off'."
    if device["type"] == "camera" and state not in ["armed", "disarmed"]:
        return f"Error: Invalid state '{state}' for camera. Use 'armed' or 'disarmed'."
        
    device["status"] = state
    if value is not None:
        if device["type"] == "light":
            device["brightness"] = value
        elif device["type"] == "thermostat":
            device["temperature"] = value
            
    return f"Success: {device['name']} has been set to '{state}'" + (f" with value '{value}'." if value else ".")

if __name__ == "__main__":
    mcp.run()
