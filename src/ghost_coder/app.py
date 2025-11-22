from nicegui import ui, app

APP_VERSION = "0.1.0"
UI_REFS = {}

async def open_native_file_dialog():
    """Called when the NiceGUI button is clicked."""
    # Use NiceGUI's native file dialog for native mode
    result = await app.native.main_window.create_file_dialog(allow_multiple=False)

    if result and len(result) > 0:
        path = result[0]
        # Update the file input to show the chosen file
        UI_REFS['file_input'].value = path
        ui.notify(f'File chosen: {path}')
        # Enable the play button since we now have a file
        UI_REFS['play_button'].enable()

        # Load file content into the editor
        try:
            with open(path, 'r', encoding='utf-8') as f:
                file_content = f.read()
                UI_REFS['code_editor'].set_content(file_content)
                # Show the right pane
                UI_REFS['right_pane'].style('display: flex;')
                UI_REFS['toggle_button'].props('icon=chevron_right')
        except Exception as e:
            ui.notify(f'Error loading file: {e}{type(e)}', type='negative')


async def select_file() -> None:
    result = await file_selector('~', multiple=True)
    ui.notify(f'You chose {result}')


@ui.page('/')
def index():
    global UI_REFS

    def toggle_right_pane():
        """Toggle the right pane visibility"""
        if UI_REFS['right_pane_visible']:
            UI_REFS['right_pane'].style('display: none;')
            UI_REFS['toggle_button'].props('icon=chevron_left')
            UI_REFS['right_pane_visible'] = False
        else:
            UI_REFS['right_pane'].style('display: flex;')
            UI_REFS['toggle_button'].props('icon=chevron_right')
            UI_REFS['right_pane_visible'] = True

    def start_playback():
        """Handle start button click"""
        print(stop_playback.__doc__)

    def pause_playback():
        """Handle pause state"""
        print(stop_playback.__doc__)

    def resume_playback():
        """Handle resume button click"""
        print(stop_playback.__doc__)

    def stop_playback():
        """Handle stop button click"""
        print(stop_playback.__doc__)

    def toggle_pause_on_new_line(e):
        """Handle pause on new line checkbox"""
        print(stop_playback.__doc__)

    def start_playback_paused(e):
        """Handle start playback paused checkbox"""
        print(stop_playback.__doc__)

    def on_advance_newline_button():
        """Handle advance to next newline button"""
        print(stop_playback.__doc__)

    def on_advance_token_button():
        """Handle advance to next token button"""
        print(stop_playback.__doc__)

    def toggle_auto_home_on_newline(e):
        """Handle auto home on newline checkbox"""
        state = "enabled" if e.value else "disabled"
        print(stop_playback.__doc__)

    def toggle_control_on_newline(e):
        """Handle control on newline checkbox"""
        state = "enabled" if e.value else "disabled"
        print(stop_playback.__doc__)

    def toggle_replace_quad_spaces_with_tab(e):
        """Handle replace quad spaces with tab checkbox"""
        state = "enabled" if e.value else "disabled"
        print(stop_playback.__doc__)

    def update_slider_label(e=None):
        """Handle changing the slider label value"""
        print(stop_playback.__doc__)

    ############################## LAYOUT ##############################
    # Main container with row layout for left and right panes
    with ui.row().classes('w-full').style('position: relative; gap: 0;'):
        # Left pane - main controls
        with ui.column().classes('p-4').style('gap: 0.1rem; flex: 1;'):
            ui.label("How to use this App:").classes('font-bold text-xl')
            ui.label("1. Select the target application")
            ui.label("2. Select the source code file to play back")
            ui.label("3. Adjust playback speed and settings")
            ui.label("4. Start playback and use controls")

            ui.separator().style("height:0.175rem;")

            ui.label("Source File to Play:").classes('font-bold')
            UI_REFS['file_input'] = ui.input(value='').props('readonly').classes('w-full')
            ui.button('Pick file (native PyWebview dialog)', icon='file_open', on_click=open_native_file_dialog)

            ui.separator().style("")

            UI_REFS['typing_speed_label'] = ui.label(f"Ghost Coding Speed: {UI_REFS['typing_speed_value']} ms").classes('font-bold')
            ui.slider(min=100, max=500, step=25, value=100, on_change=update_slider_label).classes('w-full')

            with ui.row():
                ui.checkbox("Auto Pause on New Line", value=False, on_change=toggle_pause_on_new_line)
                ui.checkbox("Start Playback Paused", value=False, on_change=start_playback_paused)

            with ui.row():
                ui.checkbox("Auto Home on Newline", value=True, on_change=toggle_auto_home_on_newline)
                ui.checkbox("Ctrl on Newline", value=True, on_change=toggle_control_on_newline)
                ui.checkbox("Replace Quad Spaces with Tab", value=True, on_change=toggle_replace_quad_spaces_with_tab)

            ui.separator().style("height:0.175rem;")

            with ui.row():
                UI_REFS['play_button'] = ui.button("START", icon='play_arrow', on_click=start_playback)
                UI_REFS['play_button'].disable()
                UI_REFS['stop_button'] = ui.button("STOP", icon='stop', on_click=stop_playback)
                UI_REFS['stop_button'].disable()
                UI_REFS['advance_to_next_newline_button'] = ui.button("ADV. NEWLINE", icon='fast_forward', on_click=on_advance_newline_button)
                UI_REFS['advance_to_next_newline_button'].disable()
                UI_REFS['advance_to_next_token_button'] = ui.button("ADV. TOKEN", icon='fast_forward', on_click=on_advance_token_button)
                UI_REFS['advance_to_next_token_button'].disable()

            ui.separator().style("height:0.175rem;")
            ui.label("Hotkeys:").classes('font-bold')

            with ui.row():
                with ui.column():
                    ui.label("Play: []").classes('font-bold')
                    ui.label("Stop: []").classes('font-bold')
                with ui.column():
                    ui.label("Adv. Token: []").classes('font-bold')
                    ui.label("Adv. to newline: []").classes('font-bold')

            ui.separator().style("height:0.175rem;")
            ui.label("Source Text/Tokens Preview Area:").classes('font-bold')
            UI_REFS['tokens_preview'] = ui.label("No content loaded yet...")

        # Toggle button (floating on the right edge)
        UI_REFS['toggle_button'] = ui.button(icon='chevron_left', on_click=toggle_right_pane).props('flat round').style(
            'position: absolute; right: 0; top: 50%; transform: translateY(-50%); z-index: 100;'
        )

        # Right pane - code editor (hidden by default)
        UI_REFS['right_pane'] = ui.column().classes('p-4').style(
            'gap: 0.1rem; flex: 1; display: none; border-left: 2px solid #ccc; height: 100vh; overflow: auto;'
        )
        with UI_REFS['right_pane']:
            ui.label("File Contents").classes('font-bold text-lg')
            UI_REFS['code_editor'] = ui.code().classes('w-full').style('height: 850px;')

def main():
    global UI_REFS
    UI_REFS = {
        'typing_speed_value': 100,
        'tokens_preview': None,
        'file_input': None,
        'play_button': None,
        'stop_button': None,
        'advance_to_next_newline_button': None,
        'advance_to_next_token_button': None,
        'typing_speed_label': None,
        'code_editor': None,
        'right_pane': None,
        'toggle_button': None,
        'right_pane_visible': False
    }

    ui.run(
        title=f"Ghost Coder {APP_VERSION}",
        native=True,
        window_size=(800, 950),
        reload=True  # Set to False so app exits when window closes
    )
if __name__ in {"__main__", "__mp_main__"}:
    main()

