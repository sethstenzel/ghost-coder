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

    def parse_string_token_to_command_token(self, string_token: str) -> Union[SingleKey, MultiKeys, TimedPause, SingleKey]:
        key_match = re.search(r"<<([a-zA-Z0-9+]+)>>", string_token)
        if key_match:
            keys = tuple(key_match.group(1).split('+'))
            if len(keys) == 1:
                return SingleKey(key=keys[0])
            return MultiKeys(keys=keys)
        
        pause_match = re.search(r"<<pause=(\d+)>>", string_token)
        if pause_match:
            return TimedPause(time=float(pause_match.group(1)))
        
        scroll_up_match = re.search(r"<<scrollup=(\d+)>>", string_token)
        if scroll_up_match:
            return MouseScroll(scroll_count=int(scroll_up_match.group(1)), scroll_direction=1)
    
        scroll_down_match = re.search(r"<<scrolldown=(\d+)>>", string_token)
        if scroll_down_match:
            return MouseScroll(scroll_count=int(scroll_down_match.group(1)), scroll_direction=-1)
        
        raise ValueError("Invalid format")
