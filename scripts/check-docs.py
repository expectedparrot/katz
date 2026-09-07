"""Check local tutorial links, responsive rules, and documented Katz command groups."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
import re
import shlex
import sys

from typer.main import get_command
from katz.cli import app

root = Path(__file__).resolve().parents[1]

class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []
        self.ids = set()
        self.text = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'id' in values:
            self.ids.add(values['id'])
        self.refs.extend(values[k] for k in ('href', 'src') if k in values)
    def handle_data(self, data):
        self.text.append(data)

errors = []
tutorial = root / 'docs/index.html'
page = Page()
source = tutorial.read_text()
page.feed(source)
for ref in page.refs:
    url = urlsplit(ref)
    if url.scheme or url.netloc:
        continue
    if url.path and not (tutorial.parent / unquote(url.path)).exists():
        errors.append(f'Missing local target: {ref}')
    elif not url.path and url.fragment and unquote(url.fragment) not in page.ids:
        errors.append(f'Missing fragment: {ref}')
for rule in ('overflow-x:auto', 'overflow-wrap:anywhere', 'name="viewport"'):
    if rule not in source:
        errors.append(f'Tutorial responsive rule absent: {rule}')
command = get_command(app)
documents = [root / 'README.md', *sorted((root/'src/katz/docs_content').glob('*.md'))]
texts = [(str(p.relative_to(root)), p.read_text()) for p in documents]
texts.append(('docs/index.html', '\n'.join(page.text)))
for filename, text in texts:
    if filename != "docs/index.html":
        text = "\n".join(re.findall(r"```(?:bash|sh|text)?\n(.*?)```", text, re.DOTALL))
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('katz '):
            continue
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue
        current = command
        for word in words[1:]:
            if word == '--human':
                continue
            if word.startswith('-') or word.startswith('<') or word.isupper():
                break
            if not hasattr(current, 'commands'):
                break
            if word not in current.commands:
                errors.append(f'{filename}: unknown command: {line}')
                break
            current = current.commands[word]
if errors:
    print('\n'.join(errors), file=sys.stderr)
    raise SystemExit(1)
print('Documentation links, command groups, and responsive source checks passed.')
