import re
from typing import Tuple, Union
from dataclasses import dataclass

@dataclass
class SingleKey:
    key: str
    
    def __str__(self):
        if self.key.upper() == "SPACE":
            return ' '
        return self.key.upper()

@dataclass
class MultiKeys:
    keys: Tuple[str, ...]

    def __str__(self):
        return "+".join(self.keys).upper()

@dataclass
class TimedPause:
    time: float

    def __str__(self):
        return f"PAUSE:{self.time}".upper()


@dataclass
class MouseScroll:
    scroll_count: int
    scroll_direction: int

    def __str__(self):
        return f"SCROLL:C{self.scroll_count}|D{self.scroll_direction}".upper()


@dataclass
class RepeatedKey:
    key: str
    count: int = 1

    def __str__(self):
        if self.count == 1:
            return self.key.upper()
        return f"{self.key.upper()}x{self.count}"


class TextData():

    def __init__(self, text_to_type="", replace_quad_spaces_with_tab=False):
        self.original_text_to_type = text_to_type
        self.replace_quad_spaces_with_tab = replace_quad_spaces_with_tab
        self.text_tokens = self.parse_text_to_tokens(self.original_text_to_type)


    def parse_text_to_tokens(self, text):
        string_tokens = self.text_to_string_tokens(text)
        parsed_tokens = []
        for string_token in string_tokens:
            try:
                command_token = self.parse_string_token_to_command_token(string_token)
                parsed_tokens.append(command_token)
            except ValueError:
                parsed_tokens.append(string_token)
        return parsed_tokens


    def text_to_string_tokens(self, text):
        if self.replace_quad_spaces_with_tab:
            text = text.replace("    ", "\t")    
        text = text.replace(" ", "<<space>>").replace("\n", "<<enter>>")
        tokens = []

        parts = re.split(f'({r'<<.*?>>'})', text)
        tokens.extend([part for part in parts if part])
        return tokens

    def parse_string_token_to_command_token(self, string_token: str) -> Union[SingleKey, MultiKeys, TimedPause, RepeatedKey, MouseScroll]:
        # Check for pause command with time value
        pause_match = re.search(r"<<pause=(\d+)>>", string_token, re.IGNORECASE)
        if pause_match:
            return TimedPause(time=float(pause_match.group(1)))

        # Check for pause command without time value (creates atpause token)
        if re.search(r"<<pause>>", string_token, re.IGNORECASE):
            return SingleKey(key="atpause")

        # Check for scroll commands
        scroll_up_match = re.search(r"<<scrollup=(\d+)>>", string_token)
        if scroll_up_match:
            return MouseScroll(scroll_count=int(scroll_up_match.group(1)), scroll_direction=1)

        scroll_down_match = re.search(r"<<scrolldown=(\d+)>>", string_token)
        if scroll_down_match:
            return MouseScroll(scroll_count=int(scroll_down_match.group(1)), scroll_direction=-1)

        # Check for backspace commands: <<BACKSPACE>> or <<BACKSPACE=N>>
        backspace_match = re.search(r"<<BACKSPACE(?:=(\d+))?>>" , string_token, re.IGNORECASE)
        if backspace_match:
            count = int(backspace_match.group(1)) if backspace_match.group(1) else 1
            return RepeatedKey(key="backspace", count=count)

        # Check for delete commands: <<DELETE>> or <<DELETE=N>>
        delete_match = re.search(r"<<DELETE(?:=(\d+))?>>", string_token, re.IGNORECASE)
        if delete_match:
            count = int(delete_match.group(1)) if delete_match.group(1) else 1
            return RepeatedKey(key="delete", count=count)

        # Check for arrow key commands: <<UP_ARROW>> or <<UP_ARROW=N>>
        up_arrow_match = re.search(r"<<UP_ARROW(?:=(\d+))?>>", string_token, re.IGNORECASE)
        if up_arrow_match:
            count = int(up_arrow_match.group(1)) if up_arrow_match.group(1) else 1
            return RepeatedKey(key="up", count=count)

        down_arrow_match = re.search(r"<<DOWN_ARROW(?:=(\d+))?>>", string_token, re.IGNORECASE)
        if down_arrow_match:
            count = int(down_arrow_match.group(1)) if down_arrow_match.group(1) else 1
            return RepeatedKey(key="down", count=count)

        left_arrow_match = re.search(r"<<LEFT_ARROW(?:=(\d+))?>>", string_token, re.IGNORECASE)
        if left_arrow_match:
            count = int(left_arrow_match.group(1)) if left_arrow_match.group(1) else 1
            return RepeatedKey(key="left", count=count)

        right_arrow_match = re.search(r"<<RIGHT_ARROW(?:=(\d+))?>>", string_token, re.IGNORECASE)
        if right_arrow_match:
            count = int(right_arrow_match.group(1)) if right_arrow_match.group(1) else 1
            return RepeatedKey(key="right", count=count)

        # Check for home key commands: <<HOME>> or <<HOME=N>>
        home_match = re.search(r"<<HOME(?:=(\d+))?>>", string_token, re.IGNORECASE)
        if home_match:
            count = int(home_match.group(1)) if home_match.group(1) else 1
            return RepeatedKey(key="home", count=count)

        # Check for end key commands: <<END>> or <<END=N>>
        end_match = re.search(r"<<END(?:=(\d+))?>>", string_token, re.IGNORECASE)
        if end_match:
            count = int(end_match.group(1)) if end_match.group(1) else 1
            return RepeatedKey(key="end", count=count)

        # Check for tab key commands: <<TAB>> or <<TAB=N>>
        tab_match = re.search(r"<<TAB(?:=(\d+))?>>", string_token, re.IGNORECASE)
        if tab_match:
            count = int(tab_match.group(1)) if tab_match.group(1) else 1
            return RepeatedKey(key="tab", count=count)

        # Check for escape key commands: <<ESC>> or <<ESCAPE>>
        if re.search(r"<<ESC(?:APE)?>>", string_token, re.IGNORECASE):
            return SingleKey(key="esc")

        # Check for enter key commands: <<ENTER>>
        if re.search(r"<<ENTER>>", string_token, re.IGNORECASE):
            return SingleKey(key="enter")

        # Check for general key patterns (must come last to avoid conflicts)
        # Updated to include special characters like backtick, tilde, brackets, etc.
        key_match = re.search(r"<<([^<>]+)>>", string_token)
        if key_match:
            keys = tuple(key_match.group(1).split('+'))

            # Map common key names to pynput Key names
            key_mapping = {
                'ctrl': 'ctrl',
                'control': 'ctrl',
                'alt': 'alt',
                'shift': 'shift',
                'win': 'cmd',  # Windows key maps to cmd in pynput
                'cmd': 'cmd',
                'super': 'cmd',
                'esc': 'esc',
                'escape': 'esc',
                'enter': 'enter',
                'return': 'enter',
                'tab': 'tab',
                'space': 'space',
                'backspace': 'backspace',
                'delete': 'delete',
                'del': 'delete',
                'home': 'home',
                'end': 'end',
                'pageup': 'page_up',
                'pagedown': 'page_down',
                'up': 'up',
                'down': 'down',
                'left': 'left',
                'right': 'right',
                'f1': 'f1', 'f2': 'f2', 'f3': 'f3', 'f4': 'f4',
                'f5': 'f5', 'f6': 'f6', 'f7': 'f7', 'f8': 'f8',
                'f9': 'f9', 'f10': 'f10', 'f11': 'f11', 'f12': 'f12',
            }

            # Normalize and map keys
            normalized_keys = []
            for key in keys:
                key_lower = key.lower()
                # Use mapping if available, otherwise use the key as-is (for regular characters)
                mapped_key = key_mapping.get(key_lower, key_lower)
                normalized_keys.append(mapped_key)

            if len(normalized_keys) == 1:
                return SingleKey(key=normalized_keys[0])
            return MultiKeys(keys=tuple(normalized_keys))

        raise ValueError("Invalid format")
