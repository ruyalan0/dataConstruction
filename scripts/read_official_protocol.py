from html.parser import HTMLParser
from pathlib import Path


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


for source in ("eval", "config"):
    parser = Text()
    parser.feed(Path(f"/tmp/astra-official-{source}.html").read_text())
    Path(f"artifacts/official-{source}.txt").write_text("\n".join(parser.parts))
