"""Repro the on-device flow path: select a plugin, tick, inspect frame."""
import pistomp.switchstate as switchstate


def test_flow_renders_on_select(v3_system):
    handler = v3_system.handler
    lcd = handler._lcd  # the Lcd320x240 wrapper
    print("grid_panel:", lcd.grid_panel)
    print("pstack.current:", type(lcd.pstack.current).__name__,
          "is main:", lcd.pstack.current is lcd.main_panel,
          "is fsw:", lcd.pstack.current is lcd.footswitch_panel)

    gp = lcd.grid_panel
    assert gp is not None
    print("tiles:", list(gp.tile_widgets.keys()))
    print("edges:", [(e.src.id, e.dst.id) for e in gp.layout.edges])

    # Navigate selection forward until a tile is selected
    for i in range(12):
        sel = gp.selected_node_id()
        if sel:
            print("selected after", i, "steps:", sel)
            break
        handler.universal_encoder_select(1)
    print("selected_node_id:", gp.selected_node_id())

    # Tick a few times and see if overlay sets a node + refreshes
    import time
    n_frames_before = len(v3_system.lcd.frames)
    for _ in range(20):
        handler.poll_lcd_updates()
        time.sleep(0.02)
    print("overlay._node:", gp._flow._node)
    print("frames before/after:", n_frames_before, len(v3_system.lcd.frames))
