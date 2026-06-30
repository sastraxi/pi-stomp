"""Registration for the caps-Eq10 plugin."""

from plugins.customization import PluginCustomization, register
from plugins.capseq10.panel import CapsEq10Panel

register(
    "http://moddevices.com/plugins/caps/Eq10",
    customization=PluginCustomization(
        panel_cls=CapsEq10Panel,
        display_name="caps-Eq10",
    ),
)
