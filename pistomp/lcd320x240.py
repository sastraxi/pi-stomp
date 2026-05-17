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

import board
import digitalio
import logging
import os
from typing import Optional
import common.token as Token
import common.parameter as Parameter
from ui.wifi_menu import WifiMenu
import pistomp.category as Category
import pistomp.lcd as abstract_lcd
import pistomp.switchstate as switchstate
from PIL import ImageColor

from uilib import *
from uilib.lcd_ili9341 import *

from pistomp.footswitch import Footswitch  # TODO would like to avoid this module knowing such details

#import traceback

class Lcd(abstract_lcd.Lcd):

    def __init__(self, cwd, handler=None, flip=False):
        self.cwd = cwd
        self.imagedir = os.path.join(cwd, "images")
        Config(os.path.join(cwd, 'ui', 'config.json'))
        self.handler = handler
        self.flip = flip

        # TODO would be good to decouple the actual LCD hardware.  This file should work for any 320x240 display
        display = LcdIli9341(board.SPI(),
                             digitalio.DigitalInOut(board.CE0),
                             digitalio.DigitalInOut(board.D6),
                             digitalio.DigitalInOut(board.D5),
                             24000000,
                             flip)

        # Colors
        self.background = (0, 0, 0)
        self.foreground = (255, 255, 255)
        self.color_splash_up = (70, 255, 70)
        self.color_splash_down = (255, 20, 20)
        self.default_plugin_color = "Silver"
        self.category_color_map = {
            'Delay': "MediumVioletRed",
            'Distortion': "Lime",
            'Dynamics': "OrangeRed",
            'Filter': (205, 133, 40),
            'Generator': "Indigo",
            'Midiutility': "Gray",
            'Modulator': (50, 50, 255),
            'Reverb': (20, 160, 255),
            'Simulator': "SaddleBrown",
            'Spacial': "Gray",
            'Spectral': "Red",
            'Utility': "Gray"
        }

        # TODO get fonts from config.json
        self.title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 26)
        self.splash_font = ImageFont.truetype('DejaVuSans.ttf', 48)
        self.small_font = ImageFont.truetype("DejaVuSans.ttf", 20)
        self.tiny_font = ImageFont.truetype("DejaVuSans.ttf", 16)
        self.title_split_orig = 190
        self.title_split = self.title_split_orig
        self.display_width = 320
        self.display_height = 240
        self.plugin_width = 78
        self.plugin_height = 29
        self.plugin_label_length = 7
        self.footswitch_height = 60
        self.footswitch_width = 56
        # space between footswitch icons where index is the footswitch count
        #                                0    1    2    3    4   5   6   7
        self.footswitch_pitch_options = [120, 120, 120, 128, 86, 65, 65, 65]
        self.footswitch_pitch = None
        self.footswitch_slots = {}

        # widgets
        self.w_wifi = None
        self._wifi_img_path: Optional[str] = None
        self.wifi_menu: Optional[WifiMenu] = None
        self.w_eq = None
        self.w_power = None
        self.w_wrench = None
        self.w_pedalboard = None
        self.w_preset = None
        self.w_plugins = []
        self.w_footswitches = []
        self.w_controls = []
        self.w_splash = None
        self.w_info_msg = None
        self.w_parameter_dialogs = {}

        # panels
        self.pstack = PanelStack(display, image_format='RGB', use_dimming=True)  # TODO use dimming without loosing FS's
        self.splash_panel = Panel(box=Box.xywh(0, 0, self.display_width, self.display_height))
        self.pstack.push_panel(self.splash_panel, refresh=False)
        self.main_panel = Panel(box=Box.xywh(0, 0, self.display_width, 170))
        self.main_panel_pushed = False
        self.footswitch_panel = Panel(box=Box.xywh(0, 176, self.display_width, 64))
        self.pstack.push_panel(self.footswitch_panel, refresh=False)

        self.pedalboards = {}

        self.wifi_menu = WifiMenu(self)

        if not display.has_system_splash:
            self.splash_show(True)

    #
    # Navigation
    #

    def enc_step_widget(self, widget, direction):
        #traceback.print_stack()
        # TODO check if widget is type
        if direction > 0:
            widget.input_event(InputEvent.RIGHT)
        elif direction < 0:
            widget.input_event(InputEvent.LEFT)

    def enc_step(self, d):
        #traceback.print_stack()
        if d > 0:
            self.pstack.input_event(InputEvent.RIGHT)
        elif d < 0:
            self.pstack.input_event(InputEvent.LEFT)

    def enc_sw(self, v):
        if v == switchstate.Value.RELEASED:
            self.pstack.input_event(InputEvent.CLICK)
        elif v == switchstate.Value.LONGPRESSED:
            self.pstack.input_event(InputEvent.LONG_CLICK)

    #
    # Main
    #
    def link_data(self, pedalboards, current, footswitches):
        self.pedalboards = pedalboards
        self.current = current
        self.footswitches = footswitches

    def draw_main_panel(self):
        self.footswitch_slots = {}
        self.draw_tools(None, None, None, None)
        self.main_panel.sel_widget(self.w_wrench)  # Make the System tool (wrench) the initial selected item
        self.draw_title()
        self.draw_analog_assignments(self.current.analog_controllers)
        self.draw_plugins()
        self.draw_unbound_footswitches()
        if not self.main_panel_pushed:
            self.pstack.push_panel(self.main_panel)
            self.main_panel_pushed = True
        #self.main_panel.refresh()

    def poll_updates(self):
        self.pstack.poll_updates()

    #
    # Toolbar
    #
    def draw_tools(self, wifi_type=None, eq_type=None, bypass_type=None, system_type=None):
        if self.w_wifi is not None:
            return
        self.w_wifi = ImageWidget(box=Box.xywh(210, 0, 20, 20), image_path=os.path.join(self.imagedir,
                                  'wifi_gray.png'), parent=self.main_panel, action=self.wifi_menu.open)
        self.main_panel.add_sel_widget(self.w_wifi)
        if self.w_eq is not None:
            return
        self.w_eq = ImageWidget(box=Box.xywh(240, 0, 20, 20), image_path=os.path.join(self.imagedir,
                                  'eq_blue.png'), parent=self.main_panel, action=self.draw_audio_menu)
        self.main_panel.add_sel_widget(self.w_eq)
        self.w_power = ImageWidget(box=Box.xywh(270, 0, 20, 20), image_path=os.path.join(self.imagedir,
                                   'power_gray.png'), parent=self.main_panel, action=self.toggle_bypass)
        self.main_panel.add_sel_widget(self.w_power)
        self.w_wrench = ImageWidget(box=Box.xywh(296, 0, 20, 20), image_path=os.path.join(self.imagedir,
                             'wrench_silver.png'), parent=self.main_panel, action=self.draw_system_menu)
        self.main_panel.add_sel_widget(self.w_wrench)

    def toggle_bypass(self, event, widget):
        if event == InputEvent.CLICK:
            self.handler.system_toggle_bypass()
        elif event == InputEvent.LONG_CLICK:
            self.draw_bypass_preference()

    def draw_bypass_preference(self):
        pref = self.handler.settings.get_setting(Token.BYPASS)
        change = self.handler.change_bypass_preference
        rows = [
            MenuRow("Left",  on_click=lambda: change(Token.LEFT),
                    active=pref == Token.LEFT),
            MenuRow("Right", on_click=lambda: change(Token.RIGHT),
                    active=pref == Token.RIGHT),
            MenuRow("Left & Right", on_click=lambda: change(Token.LEFT_RIGHT),
                    active=pref in (Token.LEFT_RIGHT, None)),
        ]
        self.pstack.push_panel(Menu(rows, title="Bypass Preference", auto_dismiss=True))

    #
    # Title (Pedalboard and Preset)
    #
    def draw_title(self):
        self.draw_pedalboard(self.current.pedalboard.title)
        self.draw_preset(self.current.presets[self.current.preset_index])
        self.draw_info_message("")  # clear loading msg
        self.main_panel.refresh()

    def draw_pedalboard(self, pedalboard_name):
        pedalboard_name += ":"
        self.title_split = min(self.title_font.getmask(pedalboard_name).getbbox()[2], self.title_split_orig)
        if self.w_pedalboard is not None:
            self.w_pedalboard.set_text(pedalboard_name)
            self.w_pedalboard.set_box(box=Box.xywh(0, 20, self.title_split, 36), realign=True, refresh=True)
            return
        self.w_pedalboard = TextWidget(box=Box.xywh(0, 20, self.title_split, 36), text=pedalboard_name,
                                       font=self.title_font, parent=self.main_panel, action=self.draw_pedalboard_menu)
        self.main_panel.add_sel_widget(self.w_pedalboard)

    def draw_preset(self, preset_name):
        x = self.title_split + 4  # title_split gets set by draw_pedalboard
        width = self.display_width - x
        if self.w_preset is not None:
            self.w_preset.set_text(preset_name)
            self.w_preset.set_box(box=Box.xywh(x, 20, width, 36), realign=True, refresh=True)
            return
        self.w_preset = TextWidget(box=Box.xywh(x, 20, width, 36), text=preset_name, font=self.title_font,
                                   parent=self.main_panel, action=self.draw_preset_menu)
        self.main_panel.add_sel_widget(self.w_preset)

    def draw_pedalboard_menu(self, event, widget):
        change = self.handler.pedalboard_change
        bank_pbs = util.DICT_GET(self.handler.get_banks(), self.handler.get_bank())
        rows: list[MenuRow] = []
        if bank_pbs is None:
            for p in self.pedalboards:
                rows.append(MenuRow(p.title, on_click=lambda p=p: change(p)))
        else:
            # Bank is set so show only those in the bank and in the order defined by the bank
            for b in bank_pbs:
                for p in self.pedalboards:  # LAME ugly O(N2) search
                    if p.title == b:
                        rows.append(MenuRow(p.title, on_click=lambda p=p: change(p)))
        self.pstack.push_panel(Menu(rows, title="Pedalboards", auto_dismiss=True, dismiss_option=True))

    def draw_preset_menu(self, event, widget):
        change = self.handler.preset_change
        rows = [MenuRow(name, on_click=lambda i=i: change(i))
                for i, name in self.current.presets.items()]
        self.pstack.push_panel(Menu(rows, title="Snapshots", auto_dismiss=True, dismiss_option=True))

    def draw_message_dialog(self, text, title="Error"):
        d = MessageDialog(self.pstack, text, title=title)
        self.pstack.push_panel(d)

    #
    # Plugins
    #
    def draw_plugins(self):
        x = 0
        y = 78
        per_row = 4
        i = 1
        # erase currently rendered plugins and footswitches first
        for w in self.w_footswitches:
            w.destroy()
        self.w_footswitches = []
        for w in self.w_plugins:
            w.destroy()
        self.w_plugins = []

        for plugin in self.current.pedalboard.plugins:
            label = plugin.instance_id.replace('/', "")[:self.plugin_label_length]
            label = label.replace("_", "")
            label = self.shorten_name(label, self.plugin_width)
            p = TextWidget(box=Box.xywh(x, y, self.plugin_width, self.plugin_height), text=label, outline_radius=5,
                           parent=self.main_panel, action=self.plugin_event, object=plugin)
            p.set_font(self.small_font)
            self.color_plugin(p, plugin)
            self.main_panel.add_sel_widget(p)
            self.w_plugins.append(p)

            pos = (i % per_row)
            x = (self.plugin_width + 2) * pos
            if pos == 0:
                y = y + self.plugin_height + 2
            i += 1

            if plugin.has_footswitch:
                self.draw_footswitch(plugin)

        self.main_panel.refresh()
        self.footswitch_panel.refresh()

    def plugin_event(self, event, widget, plugin):
        if event == InputEvent.CLICK:
            self.handler.toggle_plugin_bypass(widget, plugin)
        elif event == InputEvent.LONG_CLICK:
            self.draw_parameter_menu(plugin)


    def color_plugin(self, widget, plugin):
        color = self.get_plugin_color(plugin)
        if plugin.is_bypassed() == True:
            widget.set_outline(1, color)
            widget.set_background(self.background)
            widget.set_foreground(self.foreground)
        else:
            widget.set_outline(2, self.background)
            widget.set_background(color)
            widget.set_foreground(self.background)

    def refresh_plugins(self):
        for w in self.w_plugins:
            plugin = w.object
            self.color_plugin(w, plugin)
        self.main_panel.refresh()

    def toggle_plugin(self, widget, plugin):
        self.color_plugin(widget, plugin)
        self.main_panel.refresh()

    # Try to map color to a valid displayable color, if not use foreground
    def valid_color(self, color):
        if color is None:
            return self.foreground
        try:
            return ImageColor.getrgb(color)
        except ValueError:
            logging.error("Cannot convert color name: %s" % color)
            return self.foreground

    # Get the color assigned to the plugin category
    def get_category_color(self, category):
        color = self.default_plugin_color
        if category:
            c = util.DICT_GET(self.category_color_map, category)
            if c:
                color = c if isinstance(c, tuple) else self.valid_color(c)
        return color

    def get_plugin_color(self, plugin):
        if plugin.category:
            return self.get_category_color(plugin.category)
        return self.default_plugin_color

    #
    # Parameter Editing
    #
    def draw_parameter_menu(self, plugin):
        rows = [MenuRow(name, on_click=lambda p=param: self.draw_parameter_dialog(p))
                for name, param in sorted(plugin.parameters.items())
                if name != Token.COLON_BYPASS]
        self.pstack.push_panel(Menu(rows, title="Parameters"))

    def draw_parameter_dialog(self, parameter, timeout=None):
        # If we already have an active dialog for the parameter, use it
        d = util.DICT_GET(self.w_parameter_dialogs, parameter.name)
        if d is not None and d.parent is not None:
            return d

        # Create a new dialog
        title = parameter.instance_id + ":" + parameter.name
        current_value = parameter.value
        if parameter.type == Parameter.Type.ENUMERATION:
            rows = [MenuRow(label,
                            on_click=lambda v=value: self.parameter_commit(parameter, v),
                            active=value == current_value)
                    for label, value in parameter.get_enum_value_list()]
            d = Menu(rows, title=title, auto_dismiss=True)
            self.pstack.push_panel(d)
        elif parameter.type == Parameter.Type.TOGGLED:
            rows = [MenuRow("On",  on_click=lambda: self.parameter_commit(parameter, 1),
                            active=current_value == 1),
                    MenuRow("Off", on_click=lambda: self.parameter_commit(parameter, 0),
                            active=current_value == 0)]
            d = Menu(rows, title=title, auto_dismiss=True)
            self.pstack.push_panel(d)
        else:
            taper = 2 if parameter.type == Parameter.Type.LOGARITHMIC else 1
            d = Parameterdialog(self.pstack, parameter.name, current_value, parameter.minimum, parameter.maximum,
                                width=270, height=130, auto_destroy=True, title=title, timeout=timeout,
                                action=self.parameter_commit, object=parameter, taper=taper)
            self.pstack.push_panel(d)

        self.w_parameter_dialogs[parameter.name] = d
        return d  # return the dialog so the parameter can be modified using the tweak knob

    def parameter_commit(self, parameter, value):
        self.handler.parameter_value_commit(parameter, value)

    #
    # Footswitches
    #
    def draw_footswitch(self, plugin):
        for c in plugin.controllers:
            if isinstance(c, Footswitch):
                fs_id = c.id
                #fss[fs_id] = None
                if c.parameter.symbol != ":bypass":  # TODO token
                    label = c.parameter.name
                else:
                    label = self.shorten_name(plugin.instance_id, self.footswitch_width)
                c.set_display_label(label)

                y = 0
                x = self.get_footswitch_pitch() * fs_id
                self.footswitch_slots[fs_id] = label
                color = self.get_plugin_color(plugin)
                p = FootswitchWidget(Box.xywh(x, y, self.plugin_width, self.plugin_height), self.small_font,
                             label, color, plugin.is_bypassed(), parent=self.footswitch_panel, object=c)
                self.w_footswitches.append(p)
                self.footswitch_panel.add_widget(p)
                break

    def draw_unbound_footswitches(self):
        for fs in self.footswitches:
            if fs.id in self.footswitch_slots:
                continue
            slot = fs.id
            dl = fs.get_display_label()
            label = "" if dl is None else dl
            y = 0
            x = self.get_footswitch_pitch() * slot
            p = FootswitchWidget(Box.xywh(x, y, self.plugin_width, self.plugin_height), self.small_font,
                                 label, None, True, parent=self.footswitch_panel, object=fs)
            self.w_footswitches.append(p)
            self.footswitch_panel.add_widget(p)
        self.footswitch_panel.refresh()

    def update_footswitch(self, footswitch):
        for wfs in self.w_footswitches:
            if wfs.object == footswitch:
                wfs.toggle(footswitch.enabled == False)
                label = footswitch.get_display_label()
                if label:
                    wfs.label = label
                break
        self.footswitch_panel.refresh()
        self.refresh_plugins()  # TODO maybe not the most efficient, does exhibit some lag time

    def update_footswitches(self):
        for fs in self.footswitches:
            self.update_footswitch(fs)

    def get_footswitch_pitch(self):
        if self.footswitch_pitch is not None:
            return self.footswitch_pitch
        if self.handler:
            num_fs = self.handler.get_num_footswitches()
            if num_fs <= len(self.footswitch_pitch_options):
                self.footswitch_pitch = self.footswitch_pitch_options[self.handler.get_num_footswitches()]
                return self.footswitch_pitch
        return self.footswitch_pitch_options[-1]

    #
    # System Menu
    #
    def draw_system_menu(self, event, widget):
        h = self.handler
        rows = [
            MenuRow("System info",             on_click=self.draw_system_info_dialog),
            MenuRow("System shutdown",         on_click=lambda: h.system_menu_shutdown(None)),
            MenuRow("System reboot",           on_click=lambda: h.system_menu_reboot(None)),
            MenuRow("Restart sound engine",    on_click=lambda: h.system_menu_restart_sound(None)),
            MenuRow("Bank Select >",           on_click=lambda: self.draw_bank_menu(None)),
            MenuRow("Pedalboard Management >", on_click=self.draw_pedalboard_mgmt_menu),
        ]
        self.pstack.push_panel(Menu(rows, title="System Menu"))

    def draw_pedalboard_mgmt_menu(self):
        h = self.handler
        rows = [
            MenuRow("Save current pedalboard",    on_click=lambda: h.system_menu_save_current_pb(None)),
            MenuRow("Reload pedalboards",         on_click=lambda: h.system_menu_reload(None)),
            MenuRow("Update sample pedalboards",  on_click=self.update_sample_pedalboards),
            MenuRow("Backup data",                on_click=lambda: h.user_backup_data(None)),
            MenuRow("Restore Backup data",        on_click=lambda: h.user_restore_data(None)),
        ]
        self.pstack.push_panel(Menu(rows, title="Pedalboard Management"))

    def update_sample_pedalboards(self):
        self.pstack.pop_panel(None)
        self.draw_info_message("updating...")
        self.main_panel.refresh()
        result = self.handler.system_menu_update_sample_pedalboards()
        self.draw_info_message("")
        self.main_panel.refresh()

        # Show update stdout dialog
        d = MessageDialog(self.pstack, str(result), title="Pedalboard Update", width=250, height=140)
        self.pstack.push_panel(d)

    def draw_system_info_dialog(self):
        msg="Software:{}\nBuild:{}\nSystemState:{}\nTemperature:{}\nThrottled:{}".format(
            self.handler.software_version,
            self.handler.build_version,
            self.handler.SystemState,
            self.handler.temperature,
            self.handler.throttled)
        d = MessageDialog(self.pstack, msg, title="System Info", width=300, height=130)
        self.pstack.push_panel(d)

    def draw_bank_menu(self, event):
        current_bank = self.handler.get_bank()
        set_bank = self.handler.set_bank
        rows = [MenuRow("None (All pedalboards)",
                        on_click=lambda: set_bank(None),
                        active=current_bank is None)]
        for k in self.handler.get_banks():
            rows.append(MenuRow(k, on_click=lambda k=k: set_bank(k),
                                active=k == current_bank))
        self.pstack.push_panel(Menu(rows, title="Bank Select", auto_dismiss=True))

    def draw_audio_menu(self, event, widget):
        h = self.handler
        rows = [
            MenuRow("Output Volume",      on_click=lambda: h.system_menu_headphone_volume(None)),
            MenuRow("Input Gain",         on_click=lambda: h.system_menu_input_gain(None)),
            MenuRow("VU Calibration",     on_click=lambda: h.system_menu_vu_calibration(None)),
            MenuRow("Global EQ",          on_click=lambda: h.system_toggle_eq(None)),
            MenuRow("Low Band Gain",      on_click=lambda: h.system_menu_eq1_gain(None)),
            MenuRow("Low-Mid Band Gain",  on_click=lambda: h.system_menu_eq2_gain(None)),
            MenuRow("Mid Band Gain",      on_click=lambda: h.system_menu_eq3_gain(None)),
            MenuRow("High-Mid Band Gain", on_click=lambda: h.system_menu_eq4_gain(None)),
            MenuRow("High Band Gain",     on_click=lambda: h.system_menu_eq5_gain(None)),
        ]
        self.pstack.push_panel(Menu(rows, title="Audio Menu"))

    def draw_audio_parameter_dialog(self, name, symbol, value, min, max, commit_callback):
        d = util.DICT_GET(self.w_parameter_dialogs, symbol)
        if d is not None and d.parent is not None:
            return d

        d = Parameterdialog(self.pstack, name, value, min, max,
                            width=270, height=130, auto_destroy=True, title=name, timeout=2.2,
                            action=commit_callback, object=symbol, taper=1)
        self.w_parameter_dialogs[symbol] = d
        self.pstack.push_panel(d)
        return d

    def draw_vu_calibration_dialog(self, symbol, value, commit_callback):
        if value is None:
            value = 512  # 1024 / 2
        name = "VU Calibration"
        d = Parameterdialog(self.pstack, name, value, 502, 522,
                            width=270, height=130, auto_destroy=False, title=name, timeout=2.2,
                            action=commit_callback, object=symbol)
        self.pstack.push_panel(d)
        return d

    #
    # General
    #
    def splash_show(self, boot=True):
        self.w_splash = TextWidget(box=Box.xywh(12, 80, self.display_width, self.display_height),
                       text="pi Stomp!", font=self.splash_font, parent=self.splash_panel)
        self.w_splash.set_foreground(self.color_splash_up if boot is True else self.color_splash_down)
        self.splash_panel.refresh()

    def cleanup(self):
        self.pstack.pop_panel(None)  # current panel
        self.pstack.pop_panel(self.footswitch_panel)
        if self.main_panel_pushed:
            self.pstack.pop_panel(self.main_panel)
        if self.w_splash is not None:
            self.w_splash.set_foreground(self.color_splash_down)
            self.splash_panel.refresh()

    def clear(self):
        pass

    def erase_all(self):
        pass

    def clear_select(self):
        pass

    # Toolbar
    def update_wifi(self, wifi_status):
        if self.w_wifi is None:
            return
        if self.handler.wifi_manager.queue.pending_op_count() > 0:
            img = "wifi_processing.png"
        elif util.DICT_GET(wifi_status, 'hotspot_active'):
            img = "wifi_orange.png"
        elif util.DICT_GET(wifi_status, 'wifi_connected'):
            img = "wifi_silver.png"
        else:
            img = "wifi_gray.png"
        image_path = os.path.join(self.imagedir, img)
        if image_path == self._wifi_img_path:
            return
        self._wifi_img_path = image_path
        self.w_wifi.replace_img(image_path)

    def update_eq(self, eq_status):
        pass

    def update_bypass(self, bypass_left, bypass_right):
        if self.w_power is None:
            return
        if not bypass_left and not bypass_right:
            img = 'power_green.png'
        elif not bypass_left:
            img = 'power_left.png'
        elif not bypass_right:
            img = 'power_right.png'
        else:
            img = 'power_gray.png'
        image_path = os.path.join(self.imagedir, img)
        self.w_power.replace_img(image_path)

    def draw_tool_select(self, tool_type):
        pass

    # Menu Screens (uses deep_edit image and draw objects)
    
    def menu_show(self, page_title, menu_items):
        pass
    
    def menu_highlight(self, index):
        pass

    # Parameter Value Edit
    
    def draw_value_edit(self, plugin_name, parameter, value):
        pass

    def draw_value_edit_graph(self, parameter, value):
        pass

    # Analog Assignments (Tweak, Expression Pedal, etc.)
    def draw_analog_assignments(self, controllers):
        # Quite a few assumptions here
        # Expression pedal in first position, then 3 knobs (for v3)
        # Should work for more or fewer but won't likely look great on the LCD

        # spacing and scaling of text
        minimum = 4 if self.handler.hardware.version >= 3 else 3
        num = max(minimum, len(controllers) + 1)
        width_per_control = int(round(self.display_width / num))
        text_per_control = width_per_control - 16  # minus height of control icon

        # clean up previous control widgets
        for w in self.w_controls:
            w.destroy()

        x = 0
        y = 56  # vertical position on screen
        for i in range(0, num):
            k = None
            v = None
            for key, value in controllers.items():
                id = util.DICT_GET(value, Token.ID)
                if id is not None and int(id) == i:
                    k = key
                    v = value
                    break

            if k is None:
                # Non-mapped control
                name = "none"
                control_type = Token.EXPRESSION if i == 0 else Token.KNOB  # HACK cuz we don't know type of unmapped
                color = Category.get_category_color(None)
                text_color = color
            else:
                # Mapped control or Volume
                control_type = util.DICT_GET(v, Token.TYPE)
                if control_type == Token.VOLUME:
                    name = "volume"
                    control_type = Token.KNOB
                    color = self.default_plugin_color
                    text_color = color
                else:
                    n = k.split(":")[1]
                    name = self.shorten_name(n, text_per_control)
                    color = util.DICT_GET(v, Token.COLOR)
                    if color is None:
                        # color not specified for control in config file
                        category = util.DICT_GET(v, Token.CATEGORY)
                        text_color = Category.get_category_color(category)
                        color = self.default_plugin_color
                    else:
                        text_color = color

            if control_type == Token.KNOB:
                w = Icon(box=Box.xywh(x, y, 0, 0), text=name, text_color=text_color, parent=self.main_panel, outline=0)
                w.set_foreground(color)
                w.add_knob()
                self.w_controls.append(w)
            elif control_type == Token.EXPRESSION:
                w = Icon(box=Box.xywh(x, y, 0, 0), text=name, text_color=text_color, parent=self.main_panel, outline=0)
                w.set_foreground(color)
                w.add_pedal()
                self.w_controls.append(w)

            x += width_per_control
    
    def draw_info_message(self, text, refresh=False):
        if self.w_info_msg is None:
            self.w_info_msg = TextWidget(box=Box.xywh(0, 0, 0, 0), text='', parent=self.main_panel, outline=0,
                                         sel_width=0)
        else:
            self.w_info_msg.set_text(text)
        if refresh:
            self.main_panel.refresh()

    # Plugins
    
    def draw_plugin_select(self, plugin=None):
        pass

    def draw_bound_plugins(self, plugins, footswitches):
        pass

    def refresh_zone(self, zone_idx):
        pass
    
    def shorten_name(self, name, width):
        text = ""
        for x in name.lower().replace('_', '').replace('/', '').replace(' ', ''):
            test = text + x
            test_bbox = self.small_font.getbbox(test)
            test_size = test_bbox[2] - test_bbox[0]
            if test_size >= width:
                break
            text = test
        return text
