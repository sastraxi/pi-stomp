
from unittest.mock import MagicMock
import pytest
from ui.wifi_menu import WifiMenu
from uilib.misc import InputEvent

@pytest.mark.usefixtures("v3_system")
def test_wifi_menu_navigation(v3_system, snapshot):
    # Setup
    instance = v3_system.handler._lcd
    mock_wifi = MagicMock()
    v3_system.handler.wifi_manager = mock_wifi
    v3_system.handler.wifi_status = {"wifi_supported": True, "hotspot_active": False}
    mock_wifi.list_connections.return_value = []
    mock_wifi.scan_networks.return_value = [
        {"ssid": "NetA", "signal": 80, "security": "wpa2"},
        {"ssid": "NetB", "signal": 40, "security": "wpa2"}
    ]
    
    # Act: Open menu
    wifi_menu = WifiMenu(instance)
    wifi_menu.open()
    
    # Assert initial selection (NetA)
    snapshot("initial_menu")
    
    # Act: Navigate down (simulate encoder right)
    instance.enc_step(1)
    
    # Assert selection (NetB)
    snapshot("navigated_down")
    
    # Act: Select (simulate click)
    instance.pstack.input_event(InputEvent.CLICK)
    
    # Assert password dialog
    snapshot("password_dialog")
